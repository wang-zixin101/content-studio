"""相似内容检索：BM25 文本相关性。

检索语料的选择是这里最关键的设计：

- **判定主体**：`topic` / `subtopic` / `tags` / `keywords` ——
  这些字段在设计上就是「供检索」的（见 ANALYSIS_SCHEMA_HINT）。
  只有它们参与「是否相关」的阈值判定。
- **辅助**：`core_message` 与 `raw_text` 只以低权重（W_RAW_ASSIST）加分，
  **不参与相关性阈值判定**。
- **排序加权**：topic / content_type 只对已相关的结果加权，不能让无关内容跨过阈值。

为什么要给 `core_message` 单独划一条辅助通道（04-A 定点修正）：

`core_message` 是模型写的**叙述性摘要**，每篇都会带同一批分析框架词
（「作者抛出…」「（文中未给出理由）」「本篇…」）。同权进入判定主体时，
两篇毫无关系的内容会仅因共享「作者」就互相召回 —— 已用真实数据复现：
查询「给你100万删掉推特」与「女性外阴形态的俗称分类」只共享
`作者`、`给出`，却拿到 relevance 0.385（阈值 0.12）。
（注意：加语义停用词**不足以**解决，实测只降到 0.338，仍过阈值；
真正的修正是把叙述性摘要移出判定主体。）

为什么不把 raw_text 当同权主体：原文里「我越来越觉得」「其实」「说实话」这类
通用句式会在多篇不同主题的内容中反复出现，同权参与 BM25 会让跑步、恋爱、做饭
这些毫不相关的内容仅仅因为共享套话而被互相召回。

不使用 embedding（第一版约定），不需要第三方依赖：
中文用字符 bigram 分词（"副业赚钱" → 副业/业赚/赚钱），个人内容规模下性能足够。
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter
from typing import Any

from app import repository as repo

# ---------------- BM25 参数 ----------------
K1 = 1.5
B = 0.75

# ---------------- 归一化与阈值 ----------------
# 原始分映射到 0-1：score / (score + SOFT_K)
SOFT_K = 3.0
# 最低相关性：**只用于主体字段缺失时的原文退化通道**。
# 主体通道不用它 —— 见 find_similar 里的二值判定说明。
MIN_RELEVANCE = 0.12

# ---------------- 低权重辅助信号 ----------------
# 原文只做辅助：既不能单独让内容跨过相关性阈值，也不与语义字段同权。
# 否则「我越来越觉得」这类通用句式会把不同主题的内容互相召回。
W_RAW_ASSIST = 0.15

# ---------------- 结构化字段的少量加权 ----------------
# 叠加在**已通过阈值**的结果上，不参与「是否相关」的判定；
# content_type 同样只用于排序，不能帮助无关内容跨过阈值。
BONUS_TOPIC = 0.10
BONUS_TYPE = 0.05

# ---------------- provenance（来源）证据强度 ----------------
# 同样是排序加权，不参与阈值判定。
# 顺序：用户完全自写 / 导入本人作品 > 明显人工改写的 ai_assisted
#       > 中度修改 > 几乎原样接受
W_PROVENANCE_STRONG = 0.20      # human_original / imported_self
W_PROVENANCE_AI_HEAVY = 0.18    # ai_assisted 且修改幅度大
W_PROVENANCE_AI_MID = 0.10      # ai_assisted 且中度修改
W_PROVENANCE_AI_RAW = 0.03      # ai_assisted 几乎原样接受：弱证据
AI_HEAVY_EDIT_RATIO = 0.5
AI_MID_EDIT_RATIO = 0.2


def provenance_weight(provenance: str | None, edit_ratio: float | None) -> float:
    """把来源与修改幅度折算成排序权重。

    关键目的：防止大量「AI 写完直接保存」的内容，反过来把 AI 自己的文风
    训练成用户风格 —— 这类样本只能提供很弱的证据。
    """
    if provenance in ("human_original", "imported_self"):
        return W_PROVENANCE_STRONG
    if provenance == "ai_assisted":
        if edit_ratio is None:
            return W_PROVENANCE_AI_RAW
        if edit_ratio >= AI_HEAVY_EDIT_RATIO:
            return W_PROVENANCE_AI_HEAVY
        if edit_ratio >= AI_MID_EDIT_RATIO:
            return W_PROVENANCE_AI_MID
        return W_PROVENANCE_AI_RAW
    return 0.0

_CJK = "\u4e00-\u9fff"
_TOKEN_RE = re.compile(rf"[{_CJK}]+|[a-zA-Z0-9_]+")
_CJK_ONLY_RE = re.compile(rf"^[{_CJK}]+$")


def tokenize(text: str) -> list[str]:
    """中英混合分词：连续中文切成字符 bigram，英文/数字保留整词。"""
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(text.lower()):
        chunk = match.group(0)
        if _CJK_ONLY_RE.match(chunk):
            if len(chunk) == 1:
                tokens.append(chunk)
            else:
                tokens.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
        else:
            tokens.append(chunk)
    return tokens


# 语义字段分成**两个通道**（04-A 定点修正）
#
# ---------------------------------------------------------------- 主体
# topic / subtopic / tags / keywords —— 这些字段在设计上就是「供检索」的：
# ANALYSIS_SCHEMA_HINT 明确写着 tags/keywords 是「供搜索的主题标签与具体检索词」。
# 它们由模型对**内容本身**做概括，词面稳定、噪声低，因此承担
# 「是否相关」的判定。
_SEMANTIC_PRIMARY_FIELDS = ("topic", "subtopic")

# ---------------------------------------------------------------- 辅助
# core_message 是**叙述性摘要**（「作者抛出…」「（文中未给出理由）」），
# 每篇都会带同一批分析框架词。它确实包含内容信息，值得加分，
# **但不能参与「是否相关」的判定** ——
#
# 已复现的失效：查询「给你100万删掉推特」与「女性外阴形态的俗称分类」
# 只共享 `作者`、`给出` 两个分析框架词，却拿到 relevance 0.385（阈值 0.12），
# 于是被当成相似内容返回。根因不是停用词，而是**把叙述性摘要当成了判定主体**。
_SEMANTIC_ASSIST_FIELD = "core_message"


def _semantic_primary(record: dict[str, Any] | None) -> str:
    """判定主体语料：topic / subtopic / tags / keywords。"""
    if not record:
        return ""
    parts: list[str] = []
    for key in _SEMANTIC_PRIMARY_FIELDS:
        value = record.get(key)
        if value:
            parts.append(str(value))
    parts.extend(record.get("tags") or [])
    parts.extend(record.get("keywords") or [])
    return "\n".join(parts)


def _core_message(record: dict[str, Any] | None) -> str:
    """辅助语料：本篇核心含义（叙述性摘要，只加分、不判阈值）。"""
    if not record:
        return ""
    return str(record.get(_SEMANTIC_ASSIST_FIELD) or "")


def _semantic_text(record: dict[str, Any] | None) -> str:
    """完整的语义语料（主体 + core_message）。

    保留本函数是为了向后兼容与调试展示；**相关性判定不再直接用它**，
    见 `find_similar` 的双通道打分。
    """
    primary = _semantic_primary(record)
    core = _core_message(record)
    return "\n".join(p for p in (primary, core) if p)


def _raw_text(record: dict[str, Any] | None) -> str:
    if not record:
        return ""
    return record.get("raw_text") or ""


def _has_primary_fields(record: dict[str, Any] | None) -> bool:
    return bool(_semantic_primary(record).strip())


def _has_semantic_fields(record: dict[str, Any] | None) -> bool:
    """是否有任何语义字段（含 core_message）。保留给调用方做展示判断。"""
    return bool(_semantic_text(record).strip())


def _bm25(query_tokens: list[str], docs_tokens: list[list[str]]) -> list[float]:
    """标准 BM25 打分（idf + 长度归一化的 tf 饱和）。"""
    n = len(docs_tokens)
    if n == 0 or not query_tokens:
        return [0.0] * n

    avgdl = (sum(len(d) for d in docs_tokens) / n) or 1.0

    df: Counter[str] = Counter()
    for doc in docs_tokens:
        df.update(set(doc))

    unique_terms = set(query_tokens)
    scores: list[float] = []
    for doc in docs_tokens:
        dl = len(doc) or 1
        tf = Counter(doc)
        score = 0.0
        for term in unique_terms:
            freq = tf.get(term, 0)
            if freq == 0:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * (freq * (K1 + 1)) / (freq + K1 * (1 - B + B * dl / avgdl))
        scores.append(score)
    return scores


def _excerpt(text: str, n: int = 120) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text[:n] + "…" if len(text) > n else text


def find_similar(
    conn: sqlite3.Connection,
    target_analysis: dict[str, Any] | None,
    *,
    k: int = 5,
    exclude_id: int | None = None,
    target_text: str | None = None,
) -> list[dict[str, Any]]:
    """在「我的内容」中按 BM25 相关性找出与目标最相似的历史内容。

    返回 0 ~ k 条：相关不足时不补齐，绝不返回无关内容。
    """
    candidates = repo.mine_contents_with_analysis(conn)
    if exclude_id is not None:
        candidates = [c for c in candidates if c["id"] != exclude_id]
    if not candidates:
        return []

    # 查询：判定主体（topic/subtopic/tags/keywords）为主；
    # 主要字段全缺时退化为 core_message，再退化为原文 —— 否则这类内容永远检索不到。
    query_primary = tokenize(_semantic_primary(target_analysis))
    query_core = tokenize(_core_message(target_analysis))
    query_raw = tokenize(target_text or "")
    if not query_primary:
        query_primary = query_core or query_raw
    if not query_primary:
        return []

    primary_docs = [tokenize(_semantic_primary(c)) for c in candidates]
    core_docs = [tokenize(_core_message(c)) for c in candidates]
    raw_docs = [tokenize(_raw_text(c)) for c in candidates]

    primary_scores = _bm25(query_primary, primary_docs)
    core_scores = (
        _bm25(query_core, core_docs) if query_core else [0.0] * len(candidates)
    )
    raw_scores = _bm25(query_raw, raw_docs) if query_raw else [0.0] * len(candidates)

    analysis = target_analysis or {}
    t_topic = (analysis.get("topic") or "").strip()
    t_type = (analysis.get("content_type") or "").strip()
    t_tags = set(analysis.get("tags") or [])

    results: list[dict[str, Any]] = []
    for idx, cand in enumerate(candidates):
        primary_raw = primary_scores[idx]
        core_raw = core_scores[idx]
        raw_raw = raw_scores[idx]

        if _has_primary_fields(cand):
            # 判定主体通道：**二值判定** —— 主体语料有实质重叠才算相关。
            #
            # 为什么这里不用绝对阈值：主体语料很短（topic/subtopic/tags/keywords），
            # BM25 的 IDF 在小语料上会趋零 —— 当候选们恰好都含查询词时
            # （例如「我的内容全是副业帖」），idf = log(1 + 0.5/(n+0.5)) → 0，
            # 分数被压到 0.09 甚至 0.03，任何像 0.12 这样的绝对阈值都会误杀。
            # 已实测：真实语料下阈值取 0.01~0.12 的过滤结果完全相同 ——
            # 真正把无关内容挡在外面的是「零重叠」，不是那个数字。
            # 因此判定用重叠与否，分数只负责排序。
            if primary_raw <= 0:
                continue
            relevance = primary_raw / (primary_raw + SOFT_K)
        else:
            # 主体字段全缺：退化为原文相关性（沿用原本为该语料标定的阈值），
            # 否则这类内容永远检索不到
            relevance = raw_raw / (raw_raw + SOFT_K)
            if relevance < MIN_RELEVANCE:
                continue

        # 辅助通道：core_message 与原文都只加分，无法单独把内容捞过阈值
        core_assist = core_raw / (core_raw + SOFT_K) if core_raw > 0 else 0.0
        raw_assist = raw_raw / (raw_raw + SOFT_K) if raw_raw > 0 else 0.0
        score = relevance + W_RAW_ASSIST * (core_assist + raw_assist)

        # 来源证据强度（human_original > 明显改写 > 中度改写 > 几乎原样）
        score += provenance_weight(cand.get("provenance"), cand.get("edit_ratio"))

        # 结构化字段仅用于排序加权
        if t_topic and t_topic == (cand.get("topic") or "").strip():
            score += BONUS_TOPIC
        if t_type and t_type == (cand.get("content_type") or "").strip():
            score += BONUS_TYPE
        score = min(score, 1.0)

        results.append(
            {
                "id": cand["id"],
                "source": "mine",
                "provenance": cand.get("provenance"),
                "edit_ratio": cand.get("edit_ratio"),
                "raw_text": cand["raw_text"],
                "excerpt": _excerpt(cand["raw_text"]),
                "topic": cand.get("topic"),
                "content_type": cand.get("content_type"),
                "score": round(score, 3),
                "shared_tags": sorted(t_tags & set(cand.get("tags") or [])),
            }
        )

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:k]


def similar_for_content(
    conn: sqlite3.Connection, content_id: int, *, k: int = 5
) -> list[dict[str, Any]]:
    """给定内容 id，返回与其相似的「我的内容」。"""
    analysis = repo.get_analysis(conn, content_id)
    row = repo.get_content_raw(conn, content_id)
    text = row["raw_text"] if row else None
    return find_similar(
        conn, analysis, k=k, exclude_id=content_id, target_text=text
    )
