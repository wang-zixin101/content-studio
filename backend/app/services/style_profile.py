"""风格档案：从「我的内容」聚合统计 + 抽样原文，生成结构化个人表达档案。

两条硬规则：
1. 档案里的「修改偏好」（常删 / 常增 / 常替换的表达）**只能**来自真实修改数据
   —— 即 rewrites 表中 AI 原稿与用户终稿的实际差异。样本不足时绝不生成这类结论，
   也绝不允许模型凭空推测。
2. 档案是版本化的，每次重算生成新版本，保留历史便于观察风格演变。
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import sqlite3
from collections import Counter
from typing import Any

from app import repository as repo
from app.ai import get_provider
from app.config import STYLE_PROMPT_VERSION, settings
from app.prompts import build_style_messages
from app.services.errors import NotEnoughContentError
#: 首次建档门槛（架构 v3 A 方案）：20 篇有效已分析本人作品。
# 与 style_learning.STYLE_INITIAL_MIN 同值；写在这里避免循环导入。
STYLE_INITIAL_MIN = 20

from app.services.style_contract import (
    EDIT_PREF_KEYS,
    StyleContractError,
    validate_style_profile,
)

logger = logging.getLogger("content_studio.style_profile")

# B3：初次建档门槛与 WRITE 的 LEARNING 起点一致（5 条）。
# 3–4 条只统计可用样本数，**不启动 STYLE 模型、不声称建档** ——
# 旧值 3 会让「刚攒够 3 条就白算一版」且与 COLD_START(0–4) 重叠。
MIN_CONTENT = 5              # 硬下限：少于该数量无法形成有效档案
MAX_SAMPLES = 20             # 送入模型的原文抽样条数
SAMPLE_CHARS = 900           # 单条抽样字符预算（长稿保留头尾，见 _clip_text）
RECOMMENDED = 10             # 品质建议阈值
STYLE_REFRESH_EVERY = 5      # 每新增 N 条「已成功分析」的我的内容，触发一次自动更新
# B7：合同校验失败时的**最大尝试次数**（含首次）。
# 只做一次带着具体路径的纠正重试，禁止无界重试。
STYLE_MAX_ATTEMPTS = 2

MIN_EDIT_SAMPLES = 3         # 有效修改样本下限：少于该数量不生成任何修改偏好结论
MIN_PATTERN_COUNT = 2        # 修改模式至少要重复出现 2 次，才算「常删 / 常替换」
MAX_FRAGMENT = 30            # 修改片段最大长度，超过视为整段重写、不纳入统计
MIN_FRAGMENT = 2             # 修改片段最小长度

# 与修改行为相关的字段：没有真实修改数据时必须一律剔除
EDIT_FIELDS = (
    "deleted_expressions",
    "added_expressions",
    "replaced_expressions",
    "what_he_deletes",
    "disliked_expressions",
)


# ---------------------------------------------------------------- 统计聚合

def _counter(items: list[Any], top: int = 10) -> list[dict[str, Any]]:
    counts = Counter(x for x in items if x)
    return [{"value": k, "count": v} for k, v in counts.most_common(top)]


def aggregate_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """把多条内容的 ANALYSIS v3.1 结果聚合成统计特征。

    B1：只读取新版合同**真实存在**的字段。
    `emotion`（v3.1 已删除）不再统计；`structure` / `sentence_style`
    改为 `writing.structure_type` 与 `style.rhythm` 的扁平索引列。
    仓库的 `mine_contents_for_style` 已把这些列扁平投影出来，这里直接读。
    """
    lengths = [r.get("length_chars") or 0 for r in rows]
    lengths = [n for n in lengths if n > 0]

    tag_pool: list[str] = []
    kw_pool: list[str] = []
    for r in rows:
        tag_pool.extend(repo._loads(r.get("tags")))
        kw_pool.extend(repo._loads(r.get("keywords")))

    return {
        "sample_count": len(rows),
        "avg_length": round(sum(lengths) / len(lengths)) if lengths else 0,
        "min_length": min(lengths) if lengths else 0,
        "max_length": max(lengths) if lengths else 0,
        "content_types": _counter([r.get("content_type") for r in rows]),
        "tones": _counter([r.get("tone") for r in rows]),
        "registers": _counter([r.get("register") for r in rows]),
        "openings": _counter([r.get("opening_type") for r in rows]),
        "endings": _counter([r.get("ending_type") for r in rows]),
        "structures": _counter([r.get("structure_type") for r in rows]),
        "rhythms": _counter([r.get("rhythm") for r in rows]),
        "directness": _counter([r.get("directness") for r in rows]),
        "certainty": _counter([r.get("certainty") for r in rows]),
        "top_tags": _counter(tag_pool, top=20),
        "top_keywords": _counter(kw_pool, top=25),
    }


def _clip_text(text: str, limit: int = SAMPLE_CHARS) -> tuple[str, int | None]:
    """按预算截取文本；**保留开头与结尾**并标注截断（B5）。

    旧版一律取前 800 字，会把长帖的结尾习惯整段切掉 ——
    模型于是看不到「怎么收尾」，却仍可能被问及收尾模式。

    返回 (用于模型的文本, 原文长度或 None)。未截断时第二项为 None。
    """
    body = (text or "").strip()
    if len(body) <= limit:
        return body, None

    # 60% 给开头（开头方式最依赖开头），30% 给结尾（收尾习惯），其余留给标注
    head = body[: int(limit * 0.6)].rstrip()
    tail = body[-int(limit * 0.3):].lstrip()
    original = len(body)
    clipped = (
        f"{head}\n\n……（中间内容已截断；原文共 {original} 字，"
        f"此处仅保留开头与结尾以覆盖收尾习惯）……\n\n{tail}"
    )
    return clipped, original


def _pick_samples(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """分层抽样：**兼顾来源、话题、文体与时间**，同时**不浪费名额**（B5）。

    验收反馈修正：早期实现把"同话题最多 3 条"当成**硬性丢弃**条件，
    导致 5 篇单主题作品只抽到 3 篇、30 篇两主题作品只抽到 6 篇 ——
    名额被白白浪费。

    现在的顺序是（多样性是**优先选择**，不是丢弃条件）：
      1. 来源分层：强作者性（human_original / imported_self）优先占名额；
      2. 时间分散：时间轴均匀取样（取首尾端点）；
      3. 话题优先：先按"同话题最多 3 条"选出有代表性的样本；
      4. **名额补满**：若第 3 步没占满名额，用剩余样本补足 ——
         绝不因为"话题重复"就丢弃还有空位的名额。

    主题单一时，样本会真的全是同一主题；此时模型应当如实降低
    跨主题 Voice 的证据强度（见 STYLE_SYSTEM 证据纪律），而不是
    靠抽样伪造出主题差异。
    """
    usable = [r for r in rows if (r.get("raw_text") or "").strip()]
    if len(usable) <= MAX_SAMPLES:
        # 名额足够 → 全部使用，不做任何丢弃
        picked = usable
    else:
        picked = _stratified_sample(usable, MAX_SAMPLES)

    out: list[dict[str, Any]] = []
    for r in picked:
        text, original_length = _clip_text(r.get("raw_text") or "")
        out.append(
            {
                "content_id": r.get("id"),
                "provenance": r.get("provenance"),
                "edit_ratio": r.get("edit_ratio"),
                "topic": r.get("topic"),
                "content_type": r.get("content_type"),
                "raw_text": text,
                # 截断必须显式标注：模型不得把看不到的结尾当成「没有结尾」
                "truncated": original_length is not None,
                "original_length": original_length,
            }
        )
    return out


def _stratified_sample(
    usable: list[dict[str, Any]], budget: int
) -> list[dict[str, Any]]:
    """按来源 → 时间 → 话题三层做配额抽样，并把名额用满。"""
    strong = [
        r for r in usable
        if r.get("provenance") in ("human_original", "imported_self")
    ]
    weak = [
        r for r in usable
        if r.get("provenance") not in ("human_original", "imported_self")
    ]

    # 来源配额：**强作者性优先**（最多占 60%），但名额必须双向流通 ——
    #
    # 1. 先按"强作者性最多 60%"算出强样本名额（受实际数量限制）；
    # 2. 弱样本拿走**剩余全部**名额 —— 强样本不足时，它没用完的份额
    #    自动流给弱样本；
    # 3. 若弱样本也不足，剩余名额再归还给强样本。
    #
    # 关键：不能用固定的 `budget - 60%` 去卡弱样本 ——
    # 那样当强样本不足（0 篇或 3 篇）时，预留给强样本的份额就凭空蒸发，
    # 30 篇全 AI 辅助只抽到 8 篇、3 原创+27 AI 辅助只抽到 11 篇。
    strong_quota = max(int(budget * 0.6), 1)
    strong_budget = min(len(strong), strong_quota)
    weak_budget = min(len(weak), budget - strong_budget)
    strong_budget = min(len(strong), budget - weak_budget)

    strong_picked = (
        _spread_by_time(strong, strong_budget)
        if len(strong) > strong_budget
        else strong
    )
    weak_picked = (
        _spread_by_time(weak, weak_budget)
        if len(weak) > weak_budget
        else weak
    )
    candidates = strong_picked + weak_picked

    # 第一步：话题多样性优先（保证代表性）
    picked = _diversify_by_topic(candidates, max_per_topic=3)
    picked_ids = {id(r) for r in picked}

    # 第二步：名额没用完 → 从剩余候选补满（多样性是优先选择，不是丢弃条件）
    if len(picked) < budget:
        remaining = [r for r in candidates if id(r) not in picked_ids]
        picked += remaining[: budget - len(picked)]

    return picked[:budget]


def _spread_by_time(items: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    """时间轴均匀取样（rows 按 created_at DESC 排序，首条 = 最新）。"""
    if len(items) <= budget:
        return list(items)
    step = len(items) / budget
    indices = sorted({int(i * step) for i in range(budget)})
    return [items[i] for i in indices]


def _diversify_by_topic(
    items: list[dict[str, Any]], max_per_topic: int = 3
) -> list[dict[str, Any]]:
    """按话题优先级选代表性样本：同话题最多 max_per_topic 条。

    只负责"优先选谁"，**不负责丢弃** —— 没被选中的样本仍会在
    `_stratified_sample` 里参与名额补满。
    """
    counts: Counter[str] = Counter()
    out: list[dict[str, Any]] = []
    for r in items:
        topic = r.get("topic") or "无主题"
        if counts[topic] < max_per_topic:
            out.append(r)
            counts[topic] += 1
    return out


# ---------------------------------------------------------------- 修改模式提取

def _norm(text: str) -> str:
    return re.sub(r"[ \t]+", " ", (text or "").strip())


def compute_edit_metrics(draft: str, final: str) -> tuple[int, float]:
    """计算 AI 初稿 → 用户终稿的字符级编辑距离与修改比例。

    纯确定性计算（difflib 的 opcodes 汇总），不依赖模型，因此可用于
    判断某个 ai_assisted 样本应当获得多强的风格证据权重。
    返回 (edit_distance, edit_ratio)。
    """
    a = _norm(draft)
    b = _norm(final)
    if not a and not b:
        return 0, 0.0

    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    distance = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        distance += max(i2 - i1, j2 - j1)

    denominator = max(len(a), len(b)) or 1
    return distance, round(distance / denominator, 4)


def _meaningful(fragment: str) -> bool:
    """只统计词 / 短语级片段，避免把整段重写算作一次「删除」。"""
    stripped = fragment.strip()
    if len(stripped) < MIN_FRAGMENT or len(stripped) > MAX_FRAGMENT:
        return False
    return bool(re.search(r"[\w\u4e00-\u9fff]", stripped))


def extract_edit_patterns(
    pairs: list[tuple[str, str]], limit: int = 8
) -> dict[str, Any] | None:
    """从 (AI 原稿, 用户终稿) 的真实差异中提取修改规律。

    纯确定性计算（difflib 字符级差异），不经过模型，因此不存在编造。
    样本不足 MIN_EDIT_SAMPLES 时返回 None —— 调用方据此不生成任何修改偏好结论。

    B6：**按「不同修改对」计数，而不是按出现次数**。
    旧实现对 Counter 直接累加，同一对文章内重复出现同一动作也会把
    count 抬到 2，从而冒充「跨样本规律」。现在每个模式记录支持它的
    distinct pair 编号集合，`count` = 支持的对数。
    """
    if len(pairs) < MIN_EDIT_SAMPLES:
        return None

    deletions: dict[str, set[int]] = {}
    insertions: dict[str, set[int]] = {}
    replacements: dict[str, set[int]] = {}

    for pair_index, (draft, final) in enumerate(pairs):
        a = _norm(draft)
        b = _norm(final)
        if not a or not b or a == b:
            continue
        matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            old_frag = a[i1:i2].strip()
            new_frag = b[j1:j2].strip()
            if tag == "delete":
                if _meaningful(old_frag):
                    deletions.setdefault(old_frag, set()).add(pair_index)
            elif tag == "insert":
                if _meaningful(new_frag):
                    insertions.setdefault(new_frag, set()).add(pair_index)
            elif tag == "replace":
                if _meaningful(old_frag) and _meaningful(new_frag):
                    replacements.setdefault(f"{old_frag} → {new_frag}", set()).add(
                        pair_index
                    )

    def top(buckets: dict[str, set[int]]) -> list[dict[str, Any]]:
        """只保留**至少跨 MIN_PATTERN_COUNT 个不同修改对**的模式。"""
        return [
            {"text": key, "count": len(pair_ids)}
            for key, pair_ids in sorted(
                buckets.items(), key=lambda kv: (-len(kv[1]), kv[0])
            )
            if len(pair_ids) >= MIN_PATTERN_COUNT
        ][:limit]

    return {
        "based_on_rewrites": len(pairs),
        "frequent_deletions": top(deletions),
        "frequent_additions": top(insertions),
        "frequent_replacements": top(replacements),
    }


def _strip_unfounded_edit_fields(
    data: dict[str, Any], edit_patterns: dict[str, Any] | None
) -> dict[str, Any]:
    """安全兜底：没有真实修改数据时，剔除任何与修改偏好相关的结论。

    即使模型无视指令仍然输出，也在此剥掉 —— 保证「不凭空推测」是代码级约束，
    而不只是提示词里的请求。覆盖 V1 的扁平字段与 V2 的嵌套结构。
    """
    if edit_patterns:
        return data

    cleaned = dict(data)
    for key in EDIT_FIELDS:
        cleaned.pop(key, None)

    # V2：edit_preferences 里与修改行为有关的项一律清空
    prefs = cleaned.get("edit_preferences")
    if isinstance(prefs, dict):
        cleaned["edit_preferences"] = {
            key: (
                []
                if key.startswith("frequent_") or key == "stable_corrections"
                else value
            )
            for key, value in prefs.items()
        }

    # V2：没有修改证据，就不能声称「用户不喜欢某些表达」
    anti = cleaned.get("anti_style")
    if isinstance(anti, dict):
        anti = dict(anti)
        anti["supported_by_user_edits"] = []
        cleaned["anti_style"] = anti

    return cleaned


# ---------------------------------------------------------------- 档案生成

async def recompute_style_profile(conn: sqlite3.Connection) -> dict[str, Any]:
    """重新计算并保存一版风格档案（STYLE v3 + 严格合同校验）。

    B7：模型输出必须通过 `validate_style_profile()` 才能存档 ——
    evidence 计数由程序覆写、引文必须逐字存在于对应样本、
    枚举越界 / evidence_count 不一致都会被拒收。
    合同不通过时**只做一次**带着具体路径的纠正重试，禁止无界重试。
    """
    rows = repo.mine_contents_for_style(conn)
    if len(rows) < MIN_CONTENT:
        raise NotEnoughContentError(
            f"「我的内容」不足，当前 {len(rows)} 条，至少需要 {MIN_CONTENT} 条才能生成风格档案。"
        )

    stats = aggregate_stats(rows)
    samples = _pick_samples(rows)

    # 修改偏好只能来自真实编辑行为
    edit_pairs = repo.list_edit_pairs(conn)
    edit_patterns = extract_edit_patterns(edit_pairs)
    # 样本来源构成：让模型知道每类证据的可信度，避免把 AI 初稿特征当成用户 Voice
    provenance_stats = repo.mine_sample_stats(conn)

    # ---- B7：六个 evidence 计数由程序统计，模型不得自报 ----
    expected_counts = {
        "sample_count": len(rows),
        "text_sample_count": len(samples),
        "human_original_count": provenance_stats.get("human_original_count", 0),
        "imported_self_count": provenance_stats.get("imported_self_count", 0),
        "ai_assisted_count": provenance_stats.get("ai_assisted_count", 0),
        "rewrite_pair_count": len(edit_pairs),
    }
    # 引文逐字核对的唯一合法来源：本轮实际送来阅读的文本
    text_samples = {
        int(s["content_id"]): s["raw_text"] for s in samples if s.get("content_id")
    }

    messages = build_style_messages(stats, samples, edit_patterns, provenance_stats)
    provider = get_provider()

    data: dict[str, Any] | None = None
    last_error: StyleContractError | None = None

    for attempt in range(STYLE_MAX_ATTEMPTS):
        raw = await provider.chat_json(messages, model=settings.model_style, retries=0)
        try:
            data = validate_style_profile(
                raw, text_samples=text_samples, expected_counts=expected_counts
            )
            logger.info(
                "STYLE 合同校验通过：attempt=%s patterns=%s modes=%s",
                attempt + 1,
                len(data.get("signature_patterns") or []),
                len(data.get("x_modes") or []),
            )
            break
        except StyleContractError as err:
            last_error = err
            logger.warning("STYLE 合同校验失败：%s %s", err.path, err)
            # 只做一次带着具体路径的纠正；同参数重发不会自愈，但带着路径可以
            messages = messages + [
                {
                    "role": "assistant",
                    "content": json.dumps(raw, ensure_ascii=False),
                },
                {
                    "role": "user",
                    "content": (
                        f"上一次输出不符合 STYLE 合同：{err.path} —— {err}\n"
                        "请修正该问题后重新输出**完整**的 JSON 对象；"
                        "不要解释，不要使用 Markdown 代码围栏。"
                    ),
                },
            ]

    if data is None:
        err = last_error or StyleContractError("$", "未知原因")
        raise StyleContractError(
            err.path,
            f"STYLE 合同校验在 {STYLE_MAX_ATTEMPTS} 次尝试后仍未通过：{err}",
        )

    # ---- B6：edit_preferences 由**确定性差异投影**填充，模型无权改写 ----
    data = _apply_code_edit_preferences(data, edit_patterns, edit_pairs)

    summary = _build_summary(data)
    return repo.save_style_profile(
        conn,
        data,
        summary,
        len(rows),
        sample_ids=[int(r["id"]) for r in rows],
        prompt_version=STYLE_PROMPT_VERSION,
    )


def _apply_code_edit_preferences(
    data: dict[str, Any],
    edit_patterns: dict[str, Any] | None,
    edit_pairs: list[tuple[str, str]],
) -> dict[str, Any]:
    """用**代码生成的**修改偏好覆写模型输出（B6）。

    `frequent_*` 直接来自 `extract_edit_patterns`（确定性差异统计）；
    `stable_corrections` 只保留文本中能对应到真实差异片段的条目，
    且要求至少跨 MIN_PATTERN_COUNT 个不同修改对；
    没有真实修改数据时全部置空，`anti_style.supported_by_user_edits` 同理。
    """
    prefs = data.get("edit_preferences")
    if not isinstance(prefs, dict):
        prefs = {}
        data["edit_preferences"] = prefs

    if not edit_patterns:
        # 没有足够修改对：全部为空，绝不允许模型补
        for key in EDIT_PREF_KEYS:
            prefs[key] = []
        anti = data.get("anti_style")
        if isinstance(anti, dict):
            anti["supported_by_user_edits"] = []
        return data

    # frequent_*：代码生成的确定值，直接覆写
    for key in ("frequent_deletions", "frequent_additions", "frequent_replacements"):
        prefs[key] = edit_patterns.get(key) or []

    # stable_corrections：只保留能对应到真实差异片段的条目
    known_fragments = {
        item["text"]
        for key in ("frequent_deletions", "frequent_additions", "frequent_replacements")
        for item in prefs.get(key) or []
    }
    raw_corrections = prefs.get("stable_corrections") or []
    supported = [
        c for c in raw_corrections
        if isinstance(c, str)
        and c.strip()
        and any(fragment in c or c in fragment for fragment in known_fragments)
    ]
    prefs["stable_corrections"] = supported if supported else []

    # anti_style.supported_by_user_edits 同理：必须有真实修改证据
    anti = data.get("anti_style")
    if isinstance(anti, dict):
        raw_supported = anti.get("supported_by_user_edits") or []
        gated = [
            a for a in raw_supported
            if isinstance(a, str)
            and a.strip()
            and any(fragment in a or a in fragment for fragment in known_fragments)
        ]
        anti["supported_by_user_edits"] = gated if gated else []

    return data


def _build_summary(data: dict[str, Any]) -> str:
    """把档案压缩成一段自然语言，作为改写 prompt 的核心指导。"""
    parts: list[str] = []
    voice = data.get("voice_summary")
    if voice:
        parts.append(str(voice).strip())

    for key, label in (
        ("common_openings", "常用开头"),
        ("common_endings", "常见结尾"),
        ("common_expressions", "高频表达"),
        ("common_tones", "常用语气"),
    ):
        values = data.get(key)
        if isinstance(values, list) and values:
            joined = "、".join(str(v) for v in values[:6])
            parts.append(f"{label}：{joined}")

    # 修改偏好：仅当有真实修改数据时才有内容
    edit_patterns = data.get("edit_patterns")
    if isinstance(edit_patterns, dict):
        deletions = edit_patterns.get("frequent_deletions") or []
        if deletions:
            parts.append(
                "常删掉的表达：" + "、".join(str(d.get("text")) for d in deletions[:5])
            )
        replacements = edit_patterns.get("frequent_replacements") or []
        if replacements:
            parts.append(
                "常替换的表达：" + "、".join(str(d.get("text")) for d in replacements[:5])
            )

    return "\n".join(parts)


# ---------------------------------------------------------------- 新鲜度

#: 新版增量档案的 kind（架构 v3）；除此之外还有只读保留的 legacy_min5
NEW_PROFILE_KINDS = ("initial_v20", "incremental")


def usable_style_profile(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """返回**可用于 WRITE** 的最新风格档案；失效 / 样本不足 / 旧版时返回 None。

    四个条件缺一不可：
      1. 存在档案；
      2. **不是** `legacy_min5`（旧 5 篇门槛 + 重算式机制产出的档案）——
         它只作历史只读保留，**不得**冒充新版 20 篇有效档案；
      3. 档案的 sample_ids 里**没有**已失效样本（资格关闭 / 删除 / source 变更）；
      4. `based_on_count >= STYLE_INITIAL_MIN`（20 篇有效已分析本人作品）。

    旧档案仍完整保留在 style_profile 表中，只是不参与 WRITE。
    """
    row = conn.execute(
        "SELECT * FROM style_profile "
        " WHERE profile_kind IS NULL OR profile_kind NOT IN ('legacy_min5') "
        " ORDER BY version DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    profile = repo.latest_style_profile(conn)
    if profile is None or int(profile["id"]) != int(row["id"]):
        # 最新一行是 legacy → 取 SQL 过滤后的那一行重建
        ids_raw = row["sample_ids"]
        try:
            sample_ids = json.loads(ids_raw) if ids_raw else []
        except (json.JSONDecodeError, TypeError):
            sample_ids = []
        profile = {
            "id": row["id"],
            "version": row["version"],
            "based_on_count": row["based_on_count"],
            "profile_json": repo._safe_json(row["profile_json"]),
            "summary_text": row["summary_text"],
            "sample_ids": [int(i) for i in sample_ids],
            "prompt_version": row["prompt_version"] if "prompt_version" in row.keys() else None,
            "profile_kind": row["profile_kind"] if "profile_kind" in row.keys() else None,
            "created_at": row["created_at"],
        }

    if repo.count_invalidated_samples(conn, profile["sample_ids"]) > 0:
        return None
    if int(profile.get("based_on_count") or 0) < STYLE_INITIAL_MIN:
        return None
    return profile


def _should_run_incremental_learning(
    conn: sqlite3.Connection,
    eligible_count: int,
    initialized: bool,
    invalidated: int = 0,
) -> bool:
    """是否需要跑一次 STYLE 增量学习流程。

    注意语义：跑一次 `sync_style_learning()` **不等于**一定会调模型 ——
    它总是先 `sync_pool()` 撤销失效证据；只有当满足门槛时才会学习新增篇。
    因此"档案被失效样本污染"时这里返回 True（需要跑一次清理），
    而实际是否学习由 `sync_style_learning` 自行判断。

    延迟导入 style_learning —— 它反过来要 import 本模块的 _build_summary，
    在模块顶层互相 import 会形成循环。
    """
    from app.services.style_learning import pending_ids

    # 档案里混进了已失效样本 → 必须跑一次以撤回证据
    if invalidated > 0:
        return True
    if not pending_ids(conn):
        return False
    if initialized:
        return True
    return eligible_count >= STYLE_INITIAL_MIN


def profile_staleness(conn: sqlite3.Connection) -> dict[str, Any]:
    """档案新鲜度：是否需要重算。

    两个触发条件：

    1. **累计新增**：new_since >= STYLE_REFRESH_EVERY（保持原有策略）
    2. **样本失效**：当前档案的 sample_ids 中已有内容不再满足风格有效条件
       （用户关闭了资格 / source 改成 benchmark / 内容被删除）

    B4：第三个返回值 `usable` 标记当前档案**是否可被 WRITE 使用**。
    样本失效导致有效数量跌破门槛时，旧档案必须标记为不可用（ WRITE 禁止使用），
    而不是继续假装有效 —— 历史版本仍保留，但 WRITE 必须等重新满足条件。
    """
    profile = repo.latest_style_profile(conn)
    mine_count = repo.count_by_source(conn).get("mine", 0)
    eligible_count = repo.count_style_eligible_mine(conn)

    based_on = profile["based_on_count"] if profile else 0
    new_since = max(eligible_count - based_on, 0)

    invalidated = (
        repo.count_invalidated_samples(conn, profile["sample_ids"]) if profile else 0
    )

    # 是否已由**新版增量机制**建档（legacy_min5 不算 —— 它只作历史只读）
    initialized = bool(
        profile and profile.get("profile_kind") in NEW_PROFILE_KINDS
    )

    if profile is None:
        usable = False
    elif invalidated > 0:
        usable = False
    elif based_on < STYLE_INITIAL_MIN:
        usable = False
    elif not initialized:
        usable = False      # 旧 5 篇门槛档案不得冒充新版 20 篇有效档案
    else:
        usable = True

    return {
        "has_profile": profile is not None,
        # B4：WRITE 是否可以使用当前档案
        "usable": usable,
        "mine_count": mine_count,
        # 内容统计语义（不含风格资格），保留给 UI 参考
        "analyzed_mine_count": repo.count_analyzed_mine(conn),
        # 风格有效样本数
        "style_eligible_count": eligible_count,
        "based_on_count": based_on,
        "new_since": new_since,
        "invalidated_samples": invalidated,
        # B4 修正：失效后剩余不足 MIN_CONTENT 时应暂停重算，
        # 等待重新满足条件；而不是 `usable=False` 却 `should_recompute=True`
        # （那会让 jobs 调度器在剩余 4 条时调 STYLE 模型，白算一版）。
        # 架构 v3：旧"每新增 5 篇重算一次"已停用。
        # 现在的判据是**增量学习**：有尚未学习的新作品，且
        # （已建档 → 随时可增量 / 未建档 → 必须已达到 20 篇门槛）。
        "should_recompute": _should_run_incremental_learning(
            conn, eligible_count, initialized, invalidated
        ),
        "min_required": MIN_CONTENT,
        "recommended": RECOMMENDED,
        "refresh_every": STYLE_REFRESH_EVERY,
    }
