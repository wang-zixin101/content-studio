"""STYLE 增量学习引擎（架构 v3 / A 方案）。

------------------------------------------------------------------
业务规则（不可混淆）
------------------------------------------------------------------
1. ANALYSIS 按篇独立处理，**没有 20 篇门槛**。
2. 个人 STYLE 前 **19** 篇不启动学习；第 **20** 篇集中建立首版档案；
   第 **21** 篇起**逐篇增量**。
3. 门槛统计对象 = `source='mine'` + `style_eligible=1` +
   `analysis_state='analyzed'` 的**不同作品**。
4. 已学习且版本未变的历史作品**不重复读取、不重复计数**。
5. 对标作品**绝不**进入个人 STYLE，也不计入门槛。

------------------------------------------------------------------
与旧实现的关系
------------------------------------------------------------------
旧的 `style_profile.recompute_style_profile()` 是"定期重算 + 最多送 20 篇原文"。
本模块取代它的主路径：证据改成**持久化台账**，档案由台账投影生成。
旧档案保留在 `style_profile`（标记为 `legacy_min5`），只读、不用于 WRITE。

------------------------------------------------------------------
持久化台账
------------------------------------------------------------------
- `style_content_state`    ：逐篇学习状态（与 ANALYSIS 状态**分开**）
- `style_patterns`         ：规律（去重后的身份）
- `style_pattern_evidence` ：pattern ↔ content 的逐字证据
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from typing import Any

from app import repository as repo
from app.ai import get_provider
from app.config import settings
from app.prompts import build_style_observe_messages
from app.services.style_contract import ALLOWED_CLASSIFICATIONS

logger = logging.getLogger("content_studio.style_learning")

# ---------------------------------------------------------------- 常量

#: 首次建档门槛：**20 篇有效已分析本人作品**（架构 v3 A 方案）
STYLE_INITIAL_MIN = 20

#: 单次 API 请求送模型的原文条数上限。
#: 这只是**单次请求**的抽样上限，**不是**个人风格历史学习总量上限 ——
#: 第 1000 篇仍可增量学习，历史总量无上限。
MAX_SAMPLES_PER_REQUEST = 20

#: 单篇观察提取器版本（幂等键的一部分）
EXTRACTOR_VERSION = "obs-v1"

#: 学习状态
PENDING = "pending"
QUEUED = "queued"
PROCESSING = "processing"
LEARNED = "learned"
FAILED = "failed"
INVALIDATED = "invalidated"

ALL_STATES = (PENDING, QUEUED, PROCESSING, LEARNED, FAILED, INVALIDATED)

#: 证据类型
EVIDENCE_TYPES = ("support", "counter", "uncertain")


class StyleLearningError(Exception):
    """增量学习流程错误。"""


class ObservationContractError(Exception):
    """单篇观察不符合合同。`path` 供一次纠正重试。"""

    def __init__(self, path: str, message: str):
        self.path = path
        super().__init__(f"{path}: {message}")


# ---------------------------------------------------------------- 工具


def text_hash(text: str) -> str:
    """内容版本指纹：用于判断"同一篇是否被修稿"。

    归一化后再算：只压缩空白，不改大小写/标点 ——
    否则一次空格调整也会被当成"新版本"而重复学习。
    """
    normalized = re.sub(r"\s+", " ", (text or "")).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _now() -> str:
    return repo.now_iso()


# ---------------------------------------------------------------- 档案 dirty 标记

#: meta key：证据已变化但档案尚未（成功）重建
PROFILE_DIRTY_KEY = "style_profile_dirty"


def mark_profile_dirty(conn: sqlite3.Connection) -> None:
    """证据已变化、档案需要重建 —— 持久标记，跨进程/跨重启可恢复。"""
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, '1') "
        " ON CONFLICT(key) DO UPDATE SET value = '1'",
        (PROFILE_DIRTY_KEY,),
    )


def is_profile_dirty(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?", (PROFILE_DIRTY_KEY,)
    ).fetchone()
    return bool(row and row["value"] == "1")


def clear_profile_dirty(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM meta WHERE key = ?", (PROFILE_DIRTY_KEY,))


# ---------------------------------------------------------------- 学习池


def eligible_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """有效已分析本人作品（计入 20 篇门槛的完整记录）。

    条件：`source='mine'` AND `style_eligible=1` AND `analysis_state='analyzed'`
    且确有可用的分析结果。
    """
    rows = conn.execute(
        "SELECT c.id AS cid, c.raw_text, c.provenance, "
        "       a.topic, a.content_type, a.core_message, "
        "       a.schema_version AS analysis_schema_version, "
        "       a.prompt_version AS analysis_prompt_version "
        "  FROM contents c JOIN analyses a ON a.content_id = c.id "
        " WHERE c.source = 'mine' "
        "   AND COALESCE(c.style_eligible, 0) = 1 "
        "   AND COALESCE(c.analysis_state, 'unprocessed') = 'analyzed' "
        " ORDER BY c.created_at ASC, c.id ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def eligible_count(conn: sqlite3.Connection) -> int:
    return len(eligible_rows(conn))


def learned_ids(conn: sqlite3.Connection) -> set[int]:
    """已成功写入风格证据台账的作品 ID。"""
    rows = conn.execute(
        "SELECT content_id FROM style_content_state WHERE state = ?",
        (LEARNED,),
    ).fetchall()
    return {int(r["content_id"]) for r in rows}


def pending_ids(conn: sqlite3.Connection) -> list[int]:
    """待学习：从未学过，或内容/版本变化后需要重学。"""
    eligible = {int(r["cid"]) for r in eligible_rows(conn)}
    if not eligible:
        return []
    learned = learned_ids(conn)
    return sorted(eligible - learned)


def sync_pool(conn: sqlite3.Connection) -> dict[str, int]:
    """把学习池与当前有效作品对齐（新增 → pending；失效 → invalidated）。

    幂等键是**三元组**：正文哈希 + ANALYSIS 合同版本 + 提取器版本（问题 5）。
    - 正文修稿 → 撤回旧证据、回 pending 重学；
    - 该篇被**重新分析**（ANALYSIS 版本变化）→ 同样视为内容版本变化，
      撤回旧证据、回 pending —— 旧观察基于旧分析，已不可信；
    - 提取器版本升级 → **只更新状态行的记录值，不触发重学**
      （避免未授权时偷偷进行大规模付费回填；旧证据行的
      extractor_version 保留原值，可审计。确需回填时应明确范围）。
    """
    stats = {"inserted": 0, "invalidated": 0, "requeued": 0}
    eligible = {int(r["cid"]): r for r in eligible_rows(conn)}
    ts = _now()

    for cid, row in eligible.items():
        existing = conn.execute(
            "SELECT id, text_hash, analysis_schema_version, analysis_prompt_version, "
            "       state FROM style_content_state WHERE content_id = ?",
            (cid,),
        ).fetchone()
        current_hash = text_hash(row["raw_text"])
        current_versions = (
            row.get("analysis_schema_version"),
            row.get("analysis_prompt_version"),
        )
        if existing is None:
            conn.execute(
                "INSERT INTO style_content_state(content_id, text_hash, "
                "  analysis_schema_version, analysis_prompt_version, extractor_version, "
                "  state, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    cid, current_hash, current_versions[0], current_versions[1],
                    EXTRACTOR_VERSION, PENDING, ts, ts,
                ),
            )
            stats["inserted"] += 1
            continue

        stored_versions = (
            existing["analysis_schema_version"],
            existing["analysis_prompt_version"],
        )
        content_changed = existing["text_hash"] != current_hash
        analysis_changed = (
            existing["state"] == LEARNED and stored_versions != current_versions
        )

        if existing["state"] == LEARNED and (content_changed or analysis_changed):
            reason = "content_changed" if content_changed else "analysis_version_changed"
            # 撤回旧版本贡献，回 pending 重学（新版本内容需要重新观察）
            invalidate_content(conn, cid, reason=reason)
            conn.execute(
                "UPDATE style_content_state SET state = ?, text_hash = ?, "
                "  analysis_schema_version = ?, analysis_prompt_version = ?, "
                "  last_error = NULL, updated_at = ? WHERE content_id = ?",
                (PENDING, current_hash, current_versions[0], current_versions[1],
                 ts, cid),
            )
            stats["requeued"] += 1
        else:
            # 未学习的行也要同步最新版本号（供幂等判断）
            if (existing["text_hash"] != current_hash
                    or stored_versions != current_versions):
                conn.execute(
                    "UPDATE style_content_state SET text_hash = ?, "
                    "  analysis_schema_version = ?, analysis_prompt_version = ?, "
                    "  updated_at = ? WHERE content_id = ?",
                    (current_hash, current_versions[0], current_versions[1], ts, cid),
                )

    # 已不再有效（资格撤销 / 删除 / 转为对标）→ 撤回贡献并标记 invalidated
    placeholders_all = conn.execute(
        "SELECT content_id, state FROM style_content_state"
    ).fetchall()
    for r in placeholders_all:
        cid = int(r["content_id"])
        if r["state"] == INVALIDATED:
            continue
        if cid not in eligible:
            invalidate_content(conn, cid, reason="no_longer_eligible")
            stats["invalidated"] += 1

    return stats


def invalidate_content(
    conn: sqlite3.Connection, content_id: int, reason: str = ""
) -> int:
    """撤回某篇作品的全部证据贡献，**不重送其他旧帖原文**。

    - 删除该篇的 `style_pattern_evidence`
    - 状态置 `invalidated`
    - 失去全部证据的规律降级为 `retired`（保留历史，不物理删除）
    """
    # 先收集受影响的规律，删除后逐条刷新状态（问题 2）：
    # 只剩 1 篇支持的规律必须从 supported 降回 candidate，
    # 否则撤销后档案仍会把它当强规则用。
    affected = [
        int(r["pattern_id"])
        for r in conn.execute(
            "SELECT DISTINCT pattern_id FROM style_pattern_evidence "
            " WHERE content_id = ?",
            (content_id,),
        ).fetchall()
    ]
    removed = conn.execute(
        "DELETE FROM style_pattern_evidence WHERE content_id = ?", (content_id,)
    ).rowcount or 0

    ts = _now()
    conn.execute(
        "UPDATE style_content_state SET state = ?, last_error = ?, "
        "  observation_json = NULL, learned_at = NULL, updated_at = ? "
        " WHERE content_id = ?",
        (INVALIDATED, reason or "invalidated", ts, content_id),
    )

    for pattern_id in affected:
        refresh_pattern_status(conn, pattern_id)

    # 失去全部证据的规律降级（保留历史，不物理删除）
    conn.execute(
        "UPDATE style_patterns SET status = 'retired', updated_at = ? "
        " WHERE status != 'retired' AND id NOT IN ("
        "   SELECT DISTINCT pattern_id FROM style_pattern_evidence"
        " )",
        (ts,),
    )
    if removed:
        mark_profile_dirty(conn)
    return int(removed)


# ---------------------------------------------------------------- 规律去重


def _pattern_key(rule: str) -> str:
    """规律身份键：归一化后的 rule 哈希。

    两篇观察到"同一个写法"时 rule 文字可能略有出入，因此先归一化
    （去空白、去常见标点）再哈希 —— 让"实质相同"的规律收敛到同一条。
    """
    normalized = re.sub(r"[\s，,。、；;：:「」\"'（）()]+", "", (rule or "")).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def upsert_pattern(
    conn: sqlite3.Connection,
    *,
    rule: str,
    dimension: str | None,
    classification: str,
    applies_when: str | None,
) -> int:
    """新建或复用规律，返回 pattern_id。"""
    key = _pattern_key(rule)
    ts = _now()
    row = conn.execute(
        "SELECT id FROM style_patterns WHERE pattern_key = ?", (key,)
    ).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE style_patterns SET rule = ?, dimension = COALESCE(?, dimension), "
            "  classification = ?, applies_when = COALESCE(?, applies_when), "
            "  status = CASE WHEN status = 'retired' THEN 'candidate' ELSE status END, "
            "  updated_at = ? WHERE id = ?",
            (rule, dimension, classification, applies_when, ts, int(row["id"])),
        )
        return int(row["id"])

    cur = conn.execute(
        "INSERT INTO style_patterns(pattern_key, rule, dimension, classification, "
        "  applies_when, status, extractor_version, created_at, updated_at) "
        "VALUES(?,?,?,?,?,'candidate',?,?,?)",
        (key, rule, dimension, classification, applies_when, EXTRACTOR_VERSION, ts, ts),
    )
    return int(cur.lastrowid)


def refresh_pattern_status(conn: sqlite3.Connection, pattern_id: int) -> None:
    """按**不同作品数**刷新规律状态。

    权重不是 `weight += 1`：基础支持量 = COUNT(DISTINCT 有效 content_id)。
    同一作品重复出现同一表达只算一篇（由 UNIQUE(pattern_id, content_id) 保证）。
    """
    ts = _now()
    support = conn.execute(
        "SELECT COUNT(DISTINCT content_id) AS n FROM style_pattern_evidence "
        " WHERE pattern_id = ? AND evidence_type = 'support'",
        (pattern_id,),
    ).fetchone()
    n = int(support["n"] or 0)
    status = "supported" if n >= 2 else "candidate"
    conn.execute(
        "UPDATE style_patterns SET status = ?, updated_at = ? WHERE id = ?",
        (status, ts, pattern_id),
    )


# ---------------------------------------------------------------- 观察合同校验


def validate_observation(
    data: Any, *, raw_text: str, known_pattern_ids: set[int]
) -> list[dict[str, Any]]:
    """校验单篇观察输出。不通过则抛 `ObservationContractError`（含具体 path）。"""
    if not isinstance(data, dict):
        raise ObservationContractError("$", f"必须是 JSON 对象，实际 {type(data).__name__}")

    observations = data.get("observations")
    if not isinstance(observations, list):
        raise ObservationContractError("observations", "必须是数组")

    cleaned: list[dict[str, Any]] = []
    for i, obs in enumerate(observations):
        path = f"observations[{i}]"
        if not isinstance(obs, dict):
            raise ObservationContractError(path, "必须是对象")

        rule = obs.get("rule")
        if not isinstance(rule, str) or not rule.strip():
            raise ObservationContractError(f"{path}.rule", "必须是非空字符串")

        classification = obs.get("classification")
        if classification not in ALLOWED_CLASSIFICATIONS:
            raise ObservationContractError(
                f"{path}.classification",
                f"必须是 {'/'.join(ALLOWED_CLASSIFICATIONS)}，实际 {classification!r}",
            )

        quote = obs.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            raise ObservationContractError(f"{path}.quote", "必须是原文连续逐字片段")
        if quote not in raw_text:
            raise ObservationContractError(
                f"{path}.quote", f"引文未在原文中逐字出现：{quote[:40]!r}"
            )

        evidence_type = obs.get("evidence_type", "support")
        if evidence_type not in EVIDENCE_TYPES:
            raise ObservationContractError(
                f"{path}.evidence_type",
                f"必须是 {'/'.join(EVIDENCE_TYPES)}，实际 {evidence_type!r}",
            )

        match_id = obs.get("match_pattern_id")
        if match_id is not None:
            if isinstance(match_id, bool) or not isinstance(match_id, int):
                raise ObservationContractError(
                    f"{path}.match_pattern_id", "必须是整数或 null"
                )
            if int(match_id) not in known_pattern_ids:
                raise ObservationContractError(
                    f"{path}.match_pattern_id",
                    f"match_pattern_id {match_id} 不在提供的已有规律里（不得凭空引用）",
                )
            match_id = int(match_id)

        cleaned.append(
            {
                "rule": rule.strip(),
                "dimension": obs.get("dimension"),
                "classification": classification,
                "applies_when": obs.get("applies_when"),
                "quote": quote,
                "evidence_type": evidence_type,
                "match_pattern_id": match_id,
            }
        )

    return cleaned


# ---------------------------------------------------------------- 证据投影


def evidence_text_samples(conn: sqlite3.Connection) -> dict[int, str]:
    """所有**已学习**作品的 {content_id: 原文}，用于引文逐字核对。"""
    rows = conn.execute(
        "SELECT s.content_id AS cid, c.raw_text AS text "
        "  FROM style_content_state s JOIN contents c ON c.id = s.content_id "
        " WHERE s.state = ?",
        (LEARNED,),
    ).fetchall()
    return {int(r["cid"]): r["text"] or "" for r in rows}


def _authorship_strength(provenance: str | None, edit_ratio: float | None) -> str:
    from app.prompts import _authorship_strength as _fn

    return _fn(provenance, edit_ratio)


def _preference_strength(provenance: str | None, edit_ratio: float | None) -> str:
    from app.prompts import _preference_strength as _fn

    return _fn(provenance, edit_ratio)


def _relevant_patterns(
    conn: sqlite3.Connection,
    *,
    query_text: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """取与当前作品**语义最相关**的已有规律作为匹配候选（问题 6）。

    旧实现按 `updated_at DESC` 取最近 20 条 —— 规律库一旦增长，
    早期规律永远进不了候选，同一写法会因文字表述略有不同而被
    长期分裂成多条。

    现在用 BM25（复用 ANALYSIS 检索的同一套分词与打分）在**全量**
    未退役规律里检索，只把 top `limit` 条的**摘要**（id + rule +
    适用条件）放进单篇请求 —— 保留有限上下文，不发送整个规律库。
    查询文本为空时退化为最近更新（避免空查询打分无意义）。
    """
    rows = conn.execute(
        "SELECT id, rule, dimension, classification, applies_when, status "
        "  FROM style_patterns "
        " WHERE status != 'retired' "
        " ORDER BY updated_at DESC, id DESC"
    ).fetchall()
    out = [dict(r) for r in rows]
    if len(out) <= limit:
        return out

    query = (query_text or "").strip()
    if not query:
        return out[:limit]

    from app.services.retriever import _bm25, tokenize

    q_tokens = tokenize(query)
    if not q_tokens:
        return out[:limit]

    docs = [
        tokenize(f"{r.get('rule') or ''} {r.get('applies_when') or ''} {r.get('dimension') or ''}")
        for r in out
    ]
    scores = _bm25(q_tokens, docs)
    ranked = sorted(zip(out, scores), key=lambda pair: -pair[1])
    return [r for r, _score in ranked[:limit]]


# ---------------------------------------------------------------- 单篇学习


async def learn_one(
    conn: sqlite3.Connection,
    content_id: int,
    *,
    max_attempts: int = 2,
) -> dict[str, Any]:
    """学习**一篇**作品：提取观察 → 校验 → 写证据台账 → 标记 learned。

    幂等：已 learned 且版本指纹未变 → 直接返回，不重复调用模型。
    原子性：写库用 SAVEPOINT，失败回滚 —— **绝不留下半套风格证据**。
    可恢复性：模型 API 异常 / 超时 / 合同失败 / 写库失败，任何异常路径
    都会把状态留成 failed（问题 3），不能停在 processing。
    """
    row = conn.execute(
        "SELECT c.id AS cid, c.raw_text, c.provenance, c.style_eligible, "
        "       c.analysis_state, r.edit_ratio "
        "  FROM contents c LEFT JOIN rewrites r ON r.saved_content_id = c.id "
        " WHERE c.id = ?",
        (content_id,),
    ).fetchone()
    if row is None:
        raise StyleLearningError(f"content {content_id} 不存在")

    raw_text = row["raw_text"] or ""
    current_hash = text_hash(raw_text)
    analysis = repo.get_analysis(conn, content_id)

    state_row = conn.execute(
        "SELECT state, text_hash FROM style_content_state WHERE content_id = ?",
        (content_id,),
    ).fetchone()
    if state_row is None:
        # 尚未纳入学习池（例如绕过 sync_pool 直接调用 learn_one）→ 先建行。
        # 否则后面 UPDATE 状态会静默影响 0 行：证据写进去了、状态却没落库，
        # 台账出现"有证据无状态"的不一致。
        ts0 = _now()
        conn.execute(
            "INSERT INTO style_content_state(content_id, text_hash, "
            "  analysis_schema_version, analysis_prompt_version, extractor_version, "
            "  state, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                content_id, current_hash,
                (analysis or {}).get("schema_version") or "analysis-v3",
                (analysis or {}).get("prompt_version"),
                EXTRACTOR_VERSION, PENDING, ts0, ts0,
            ),
        )
    elif (
        conn.execute(
            "SELECT 1 FROM style_content_state WHERE content_id = ? "
            "  AND state = ? AND text_hash = ?",
            (content_id, LEARNED, current_hash),
        ).fetchone()
        is not None
    ):
        return {"content_id": content_id, "status": "skipped",
                "reason": "已学习且版本未变（幂等）"}

    ts = _now()
    conn.execute(
        "UPDATE style_content_state SET state = ?, last_error = NULL, updated_at = ? "
        " WHERE content_id = ?",
        (PROCESSING, ts, content_id),
    )

    observations: list[dict[str, Any]] | None = None
    last_error: Exception | None = None
    touched: set[int] = set()

    # 本轮验收边界 2：get_provider()、已有规律检索、构造提示词等
    # **前置处理**也纳入失败兜底 —— 这些步骤发生在标记 processing 之后，
    # 任何一步异常都必须把状态留成 failed（可恢复），不能停在 processing。
    try:
        known_patterns = _relevant_patterns(
            conn,
            query_text=(analysis or {}).get("core_message") or raw_text[:300],
        )
        known = {int(p["id"]) for p in known_patterns}
        messages = build_style_observe_messages(
            raw_text=raw_text,
            analysis=analysis,
            existing_patterns=known_patterns,
            provenance=row["provenance"],
            edit_ratio=row["edit_ratio"],
        )

        provider = get_provider()
        for attempt in range(max_attempts):
            raw = await provider.chat_json(
                messages, model=settings.model_style, retries=0
            )
            try:
                observations = validate_observation(
                    raw, raw_text=raw_text, known_pattern_ids=known
                )
                logger.info(
                    "单篇观察校验通过：content=%s attempt=%s observations=%s",
                    content_id, attempt + 1, len(observations),
                )
                break
            except ObservationContractError as err:
                last_error = err
                logger.warning("单篇观察校验失败：content=%s %s %s",
                               content_id, err.path, err)
                messages = messages + [
                    {"role": "assistant",
                     "content": json.dumps(raw, ensure_ascii=False)},
                    {"role": "user", "content": (
                        f"上一次输出不符合观察合同：{err.path} —— {err}\n"
                        "请修正后重新输出**完整** JSON；不要解释，不要代码围栏。"
                    )},
                ]

        if observations is None:
            err = last_error or ObservationContractError("$", "未知原因")
            raise ObservationContractError(
                err.path,
                f"单篇观察在 {max_attempts} 次尝试后仍未通过：{err}",
            )

        # ---- 原子写入：证据 + 规律 + 状态 ----
        conn.execute("SAVEPOINT style_learn_one")
        try:
            conn.execute(
                "DELETE FROM style_pattern_evidence WHERE content_id = ?",
                (content_id,),
            )
            authorship = _authorship_strength(row["provenance"], row["edit_ratio"])
            preference = _preference_strength(row["provenance"], row["edit_ratio"])
            topic = (analysis or {}).get("topic")

            for obs in observations:
                if obs["match_pattern_id"] is not None:
                    pattern_id = int(obs["match_pattern_id"])
                    conn.execute(
                        "UPDATE style_patterns SET updated_at = ? WHERE id = ?",
                        (_now(), pattern_id),
                    )
                else:
                    pattern_id = upsert_pattern(
                        conn,
                        rule=obs["rule"],
                        dimension=obs["dimension"],
                        classification=obs["classification"],
                        applies_when=obs["applies_when"],
                    )
                touched.add(pattern_id)

                conn.execute(
                    "INSERT OR REPLACE INTO style_pattern_evidence("
                    "  pattern_id, content_id, quote, source_kind, authorship, "
                    "  preference, topic, evidence_type, text_hash, "
                    "  extractor_version, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        pattern_id, content_id, obs["quote"], row["provenance"],
                        authorship, preference, topic, obs["evidence_type"],
                        current_hash, EXTRACTOR_VERSION, _now(),
                    ),
                )

            for pattern_id in touched:
                refresh_pattern_status(conn, pattern_id)

            conn.execute(
                "UPDATE style_content_state SET state = ?, text_hash = ?, "
                "  observation_json = ?, learned_at = ?, last_error = NULL, "
                "  updated_at = ? WHERE content_id = ?",
                (
                    LEARNED, current_hash,
                    json.dumps({"observations": observations}, ensure_ascii=False),
                    _now(), _now(), content_id,
                ),
            )
            conn.execute("RELEASE SAVEPOINT style_learn_one")
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT style_learn_one")
            conn.execute("RELEASE SAVEPOINT style_learn_one")
            raise

        # 证据已变化 → 档案需要重建（持久标记：即使随后的
        # build_profile_from_evidence 失败，下一次 sync 也能仅重试档案，
        # 不重新调用 AI 学习已完成作品）
        mark_profile_dirty(conn)
    except Exception as err:  # noqa: BLE001
        # 统一兜底：任何异常（API / 超时 / 合同 / 写库）→ failed，可重试
        err_text = f"{type(err).__name__}: {str(err)[:300]}"
        try:
            conn.execute(
                "UPDATE style_content_state SET state = ?, last_error = ?, "
                "  updated_at = ? WHERE content_id = ?",
                (FAILED, err_text, _now(), content_id),
            )
        except Exception:  # noqa: BLE001 —— 状态更新失败也不掩盖原始异常
            logger.exception("标记 failed 失败：content=%s", content_id)
        if isinstance(err, ObservationContractError):
            raise
        raise StyleLearningError(err_text) from err

    return {
        "content_id": content_id,
        "status": "learned",
        "observations": len(observations),
        "patterns_touched": len(touched),
    }


# ---------------------------------------------------------------- 建档 / 增量入口


def _has_initial_profile(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM style_profile WHERE profile_kind = 'initial_v20'"
    ).fetchone()
    return int(row["n"] or 0) > 0


async def _run_batch(conn: sqlite3.Connection, ids: list[int]) -> list[dict[str, Any]]:
    results = []
    for cid in ids:
        try:
            results.append(await learn_one(conn, cid))
        except Exception as err:  # noqa: BLE001
            results.append(
                {"content_id": cid, "status": "failed",
                 "error": f"{type(err).__name__}: {str(err)[:200]}"}
            )
    return results


async def sync_style_learning(conn: sqlite3.Connection) -> dict[str, Any]:
    """统一的 STYLE 学习入口（ANALYSIS 完成 / 已分析结果回导后调用）。

    - 同步学习池（新增 → pending；失效/版本变化 → invalidated 或重学）
    - 有效作品 **< 20** → 只清理，不学习（门槛硬性，无论是否已建档）
    - 首次建档（从未有 initial_v20）：**必须 pending 全部成功**才发布
      档案（问题 1）；部分失败时保留已完成部分，重试只补失败作品
    - 已建档：只学新增篇（逐篇增量）；**证据发生变化（含撤销/修稿）后
      即使没有待学新作品，也必须重建档案**（问题 2）
    """
    pool = sync_pool(conn)
    count = eligible_count(conn)
    initialized = _has_initial_profile(conn)

    # 门槛是**硬性**的：有效数 < 20 一律不学习（只做清理）。
    # 建档后若因撤销导致跌破 20，也必须暂停 ——
    # 不得在 19 篇时继续"增量"，那等于绕过首次门槛白算一版。
    if count < STYLE_INITIAL_MIN:
        return {
            "action": "waiting",
            "eligible_count": count,
            "min_required": STYLE_INITIAL_MIN,
            "initialized": initialized,
            "learned": 0,
            "pool": pool,
            "reason": (
                f"有效已分析本人作品 {count} 篇 < {STYLE_INITIAL_MIN}，暂不启动 STYLE"
                + ("（已建档但跌破门槛，等待重新满足）" if initialized else "")
            ),
        }

    pending = pending_ids(conn)

    # ---- 首次建档：pending 必须全部成功，才发布 initial_v20（问题 1）----
    if not initialized:
        profile_dirty = is_profile_dirty(conn)
        if not pending and not profile_dirty:
            # 防御：从未建档却没有待学（理论不可达，例如证据被手工清空）
            return {
                "action": "noop",
                "eligible_count": count,
                "initialized": False,
                "learned": 0,
                "pool": pool,
                "reason": "首次建档需要待学作品，但学习池为空",
            }

        results: list[dict[str, Any]] = []
        learned_n = 0
        total_learned = len(learned_ids(conn))

        if pending:
            # **只学待学篇** —— 已成功保存证据的作品绝不重新调用 AI。
            results = await _run_batch(conn, pending)
            learned_n = sum(1 for r in results if r.get("status") == "learned")
            total_learned = len(learned_ids(conn))

            if learned_n < len(pending) or total_learned < STYLE_INITIAL_MIN:
                # 部分失败：**不发布**首版档案（已独立复现的场景）。
                # 已成功的作品保留 learned 状态与证据；重试只补失败作品。
                failed = [r for r in results if r.get("status") != "learned"]
                return {
                    "action": "initial_partial",
                    "eligible_count": count,
                    "initialized": False,
                    "pending": pending,
                    "learned": learned_n,
                    "total_learned": total_learned,
                    "results": results,
                    "failed": failed,
                    "pool": pool,
                    "profile_version": None,
                    "reason": (
                        f"首次建档要求 {len(pending)} 篇全部成功，"
                        f"实际成功 {learned_n}（台账累计 {total_learned}）；"
                        f"首版档案**未发布**，重试只补失败作品"
                    ),
                }

        profile = None
        profile_error = None
        try:
            profile = build_profile_from_evidence(conn, kind="initial_v20")
        except Exception as err:  # noqa: BLE001
            profile_error = f"{type(err).__name__}: {str(err)[:200]}"
            logger.warning("首版档案生成失败（证据已全部落库）：%s", profile_error)

        return {
            "action": "initial",
            "eligible_count": count,
            "initialized": profile is not None,
            "pending": pending,
            "learned": learned_n,
            "total_learned": total_learned,
            "results": results,
            "pool": pool,
            "profile_version": (profile or {}).get("version"),
            "profile_error": profile_error,
        }

    # ---- 增量：只学新增篇；证据变化（含撤销/修稿）后必须重建档案 ----
    results: list[dict[str, Any]] = []
    learned_n = 0
    if pending:
        results = await _run_batch(conn, pending)
        learned_n = sum(1 for r in results if r.get("status") == "learned")

    # 问题 2：撤销/修稿会撤回证据（pool.invalidated / requeued > 0），
    # 即使没有任何待学新作品，也必须重建档案 —— 否则最新档案仍然
    # 包含已撤销作品、只剩 1 篇支持的规律仍标 supported。
    # dirty（证据已保存但上次档案生成失败）同样必须触发重建，
    # 否则会因 pending 为空而永久 noop（本轮验收边界 1）。
    evidence_changed = (
        learned_n > 0
        or pool["invalidated"] > 0
        or pool["requeued"] > 0
        or is_profile_dirty(conn)
    )
    if not evidence_changed:
        return {
            "action": "noop",
            "eligible_count": count,
            "initialized": True,
            "learned": 0,
            "pool": pool,
            "reason": "没有待学习的新作品，证据也无变化",
        }

    profile = None
    profile_error = None
    try:
        profile = build_profile_from_evidence(conn, kind="incremental")
    except Exception as err:  # noqa: BLE001
        profile_error = f"{type(err).__name__}: {str(err)[:200]}"
        logger.warning("增量档案重建失败（证据已保存）：%s", profile_error)

    return {
        "action": "incremental",
        "eligible_count": count,
        "initialized": True,
        "pending": pending,
        "learned": learned_n,
        "results": results,
        "pool": pool,
        "profile_version": (profile or {}).get("version"),
        "profile_error": profile_error,
    }


def build_profile_from_evidence(
    conn: sqlite3.Connection, kind: str = "incremental"
) -> dict[str, Any]:
    """由**证据台账**投影生成一版 STYLE v3 档案（不重送原文给模型）。

    规律→signature_patterns；证据→supporting_samples（逐字引文）；
    计数由 SQL 聚合（COUNT DISTINCT），不是模型自报。
    """
    from app.services.style_contract import validate_style_profile
    from app.services.style_profile import _build_summary

    text_samples = evidence_text_samples(conn)
    eligible = eligible_rows(conn)
    provenance_counts = {
        "human_original": 0, "imported_self": 0, "ai_assisted": 0,
    }
    for r in eligible:
        key = r.get("provenance")
        if key in provenance_counts:
            provenance_counts[key] += 1

    topic_count = len({r.get("topic") for r in eligible if r.get("topic")})
    learned = len(text_samples)

    # 问题 4：修改偏好**不能固定清空** —— 继续从真实 AI 初稿→终稿差异
    # 确定性生成（04-B 已完成的能力），绝不允许 AI 编造。
    from app.services.style_profile import extract_edit_patterns

    edit_pairs = repo.list_edit_pairs(conn)
    edit_patterns = extract_edit_patterns(edit_pairs) or {
        "frequent_deletions": [], "frequent_additions": [],
        "frequent_replacements": [],
    }

    rows = conn.execute(
        "SELECT p.id, p.rule, p.classification, p.applies_when, p.status, "
        "       COUNT(DISTINCT e.content_id) AS support_count "
        "  FROM style_patterns p "
        "  LEFT JOIN style_pattern_evidence e "
        "         ON e.pattern_id = p.id AND e.evidence_type = 'support' "
        " WHERE p.status != 'retired' "
        " GROUP BY p.id "
        " ORDER BY support_count DESC, p.id ASC"
    ).fetchall()

    signature_patterns = []
    for r in rows:
        quotes = conn.execute(
            "SELECT content_id, quote FROM style_pattern_evidence "
            " WHERE pattern_id = ? AND evidence_type = 'support'",
            (int(r["id"]),),
        ).fetchall()
        supporting = [
            {"content_id": int(q["content_id"]), "quote": q["quote"]}
            for q in quotes
            if q["quote"] in text_samples.get(int(q["content_id"]), "")
        ]
        if not supporting:
            continue
        # 强度：由**不同作品数**决定，不靠出现次数
        n = len(supporting)
        strength = "strong" if n >= 3 else ("medium" if n == 2 else "weak")
        signature_patterns.append(
            {
                "rule": r["rule"],
                "classification": r["classification"],
                "strength": strength,
                "frequency": f"已学习的 {learned} 篇中 {n} 篇存在",
                "evidence_count": n,
                "applies_when": r["applies_when"] or "未限定",
                "supporting_samples": supporting,
            }
        )

    uncertain = [
        r["rule"] for r in rows
        if r["classification"] == "UNCERTAIN"
    ]

    confidence = "low"
    if learned >= 20 and topic_count >= 3:
        confidence = "high" if learned >= 30 else "medium"

    profile_json = {
        "common_openings": [],
        "common_endings": [],
        "common_structures": [],
        "avg_length": None,
        "long_short_ratio": None,
        "first_person_habit": None,
        "common_tones": [],
        "common_expressions": [],
        "high_freq_words": [],
        "emotion_profile": None,
        "viewpoint_style": None,
        "common_content_types": sorted(
            {r.get("content_type") for r in eligible if r.get("content_type")}
        ),
        "evidence": {
            "sample_count": len(eligible),
            "text_sample_count": learned,
            "human_original_count": provenance_counts["human_original"],
            "imported_self_count": provenance_counts["imported_self"],
            "ai_assisted_count": provenance_counts["ai_assisted"],
            "rewrite_pair_count": len(edit_pairs),
            "confidence": confidence,
        },
        "global_voice": {
            "register": None, "stance": None, "syntax_rhythm": None,
            "lexical_fingerprint": None, "rhetorical_patterns": [],
            "formatting": None, "engagement": None,
        },
        "signature_patterns": signature_patterns,
        "x_modes": [],
        "edit_preferences": {
            # 问题 4：来自真实差异的确定性投影（extract_edit_patterns），
            # 不是模型输出；无修改数据时自然为空数组
            "frequent_deletions": edit_patterns.get("frequent_deletions", []),
            "frequent_additions": edit_patterns.get("frequent_additions", []),
            "frequent_replacements": edit_patterns.get("frequent_replacements", []),
            "stable_corrections": [],
        },
        "anti_style": {"supported_by_user_edits": [], "not_observed_do_not_force": []},
        "topic_bound_terms": [r["rule"] for r in rows
                              if r["classification"] == "TOPIC_BOUND"],
        "uncertain_patterns": uncertain,
        "voice_summary": _build_voice_summary(signature_patterns, learned, topic_count),
    }

    validated = validate_style_profile(
        profile_json,
        text_samples=text_samples,
        expected_counts={
            "sample_count": len(eligible),
            "text_sample_count": learned,
            "human_original_count": provenance_counts["human_original"],
            "imported_self_count": provenance_counts["imported_self"],
            "ai_assisted_count": provenance_counts["ai_assisted"],
            "rewrite_pair_count": len(edit_pairs),
        },
    )

    summary = _build_summary(validated)
    from app.config import STYLE_PROMPT_VERSION

    result = repo.save_style_profile(
        conn,
        validated,
        summary,
        learned,
        sample_ids=sorted(text_samples.keys()),
        prompt_version=STYLE_PROMPT_VERSION,
        profile_kind=kind,
    )
    # 档案已与证据台账对齐 → 清除 dirty
    clear_profile_dirty(conn)
    return result


def _build_voice_summary(patterns: list[dict], learned: int, topic_count: int) -> str | None:
    if not patterns:
        return None
    strong = [p for p in patterns if p["strength"] == "strong"]
    lines = [
        f"基于已学习 {learned} 篇（覆盖 {topic_count} 个话题）的增量证据："
    ]
    for p in (strong or patterns)[:5]:
        lines.append(f"· {p['rule']}（{p['classification']}，适用：{p['applies_when']}）")
    if topic_count < 3:
        lines.append(
            f"注意：目前仅覆盖 {topic_count} 个话题，跨话题稳定 Voice 尚无充分证据，"
            "不要过度外推。"
        )
    return "\n".join(lines)
