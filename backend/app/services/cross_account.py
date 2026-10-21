"""跨账号流量画像（阶段 9）：把多个作者的 Stage 8 画像横向聚合。

------------------------------------------------------------------
0 AI / 0 token
------------------------------------------------------------------
本模块只做确定性统计：读每个作者已经算好的 Stage 8 单账号画像，
在内存里做跨账号聚合。不重新分析、不补分析、不生成总结、不做推荐。

------------------------------------------------------------------
核心纪律 1：先账号内标准化，再跨账号比较
------------------------------------------------------------------
绝不能直接比较绝对曝光（A 5 万 vs B 5 万没有意义）。
只使用 Stage 8 已经账号内标准化的量：
    Performance Score / median_performance_score / top10_rate / bottom40_rate

------------------------------------------------------------------
核心纪律 2：作者等权，一个作者 = 一票
------------------------------------------------------------------
A：N=200、中位分 80
B：N=20、中位分 60
→ 跨账号中位 = median(80, 60) = 70
**绝不**把 220 条帖子混起来重算，否则大账号会用帖子数淹没小账号。
sample_count 只是证据量（total_sample_count 仅供展示），不做权重。

------------------------------------------------------------------
核心纪律 3：作者内部仍要过 N>=5
------------------------------------------------------------------
某 value 在某作者内部 sample_count < 5 → 该作者对此 value 没有有效判断，
**不贡献** author-level observation。沿用 Stage 8 的 MIN_RANKING_SAMPLE。
"""

from __future__ import annotations

import re
import sqlite3
from statistics import median
from typing import Any

from app import repository as repo
from app.services import performance_profile as perf

# ---------------------------------------------------------------- 常量

# 进入跨账号「共同规律」的最低作者数
COMMON_PATTERN_MIN_AUTHORS = 3
# 账号依赖型最低 2 个作者（两账号方向相反已具有比较价值）
ACCOUNT_DEPENDENT_MIN_AUTHORS = 2

# positive / negative 判定沿用 Stage 8 的四象限口径
POSITIVE_SCORE = perf.HIGH_PERF_SCORE      # >= 60
NEGATIVE_SCORE = perf.LOW_PERF_SCORE       # < 40

# 作者内部最低样本量（与 Stage 8 同一门槛）
MIN_AUTHOR_SAMPLE = perf.MIN_RANKING_SAMPLE

DIMENSION_VALUE_LIMIT = 50                 # 单维度 all_values 最多返回多少取值
RANKING_LIMIT = 10                         # 三张榜各自最多多少条

# 三类标签是**独立 flag**，可以重叠；mixed 只是「三个 flag 全 false」的展示标签
CLASS_COMMON_HIGH = "common_high"
CLASS_COMMON_LOW = "common_low"
CLASS_ACCOUNT_DEPENDENT = "account_dependent"
CLASS_MIXED = "mixed"

# ---------------------------------------------------------------- 维度白名单

# V1 只铺这些维度：Stage 8 有 35 个 writing metric，
# 全部铺满会把页面变成噪音；先回答「什么内容好 / 什么开头好 / 什么结构好 /
# 什么修辞动作好 / 什么语气句式好」。
CONTENT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("topic", "主题", "categorical"),
    ("content_type", "内容类型", "categorical"),
)

WRITING_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("writing.opening_type", "开头方式", "categorical"),
    ("writing.structure_type", "整体结构", "categorical"),
    ("writing.moves[].type", "修辞 / 互动动作", "list"),
    ("writing.ending_type", "结尾方式", "categorical"),
    ("style.tone", "语气", "categorical"),
    ("style.register", "语体", "categorical"),
    ("style.directness", "直接程度", "categorical"),
    ("style.certainty", "确定性", "categorical"),
    ("style.rhythm", "句式节奏", "categorical"),
    ("style.markers[].type", "表达标记", "list"),
)

ALL_FIELDS = CONTENT_FIELDS + WRITING_FIELDS

# ---------------------------------------------------------------- 取值规范化

_WS = re.compile(r"\s+")


def normalize_value(value: str) -> str:
    """确定性清洗：trim → 连续空白标准化 → 大小写标准化。

    只做这三项。不做同义词映射、不做 topic clustering、不建 taxonomy——
    「Claude」与「Claude 相关」不会被自动合并。
    宁可少合并，也绝不伪造跨账号共性。
    """
    return _WS.sub(" ", str(value).strip()).casefold()


def author_direction(score: float) -> str:
    """沿用 Stage 8 四象限的表现口径。"""
    if score >= POSITIVE_SCORE:
        return "positive"
    if score < NEGATIVE_SCORE:
        return "negative"
    return "neutral"


# ---------------------------------------------------------------- 取值聚合

def _observation(
    author: dict[str, Any],
    samples: list[dict[str, Any]],
    evidence_values: list[str],
) -> dict[str, Any]:
    """把某作者在**某个 normalized 取值下的全部实际样本**聚合成一条 observation。

    关键点：
      - 传进来的是该作者的实际样本，不是 Stage 8 各 raw 取值的中位分。
        因此同一作者的多个 raw 变体（GPT / gpt / " Gpt "）合并后，
        sample_count / 中位分 / 中位曝光 / Top10%·Bottom40% 都是
        对合并后真实样本的正确聚合，**不是**「中位分再取中位」。
      - 复用 Stage 8 的 aggregate_samples，口径与单账号画像完全一致。
    """
    stats = perf.aggregate_samples(samples)
    score = float(stats["median_performance_score"])
    return {
        "author_id": int(author["id"]),
        "username": author.get("username"),
        "display_name": author.get("display_name"),
        **stats,
        "direction": author_direction(score),
        # Stage 8 evidence 需要的是**原始**取值，不能用 normalized identity
        "evidence_values": evidence_values,
    }


def classify_flags(
    author_count: int,
    median_author_score: float,
    positive_count: int,
    negative_count: int,
    min_score: float,
    max_score: float,
) -> dict[str, bool]:
    """三个 flag **互相独立**，绝不用 if/elif 强制四选一。

    同一个 value 完全可以既是「跨账号高表现」又是「账号依赖明显」——
    例如 85 / 80 / 25：多数账号很好，但有一个账号明显反向，
    这两条信息同时成立、都重要。
    """
    return {
        "is_common_high": bool(
            author_count >= COMMON_PATTERN_MIN_AUTHORS
            and median_author_score >= POSITIVE_SCORE
            and positive_count * 3 >= author_count * 2      # >= 2/3
        ),
        "is_common_low": bool(
            author_count >= COMMON_PATTERN_MIN_AUTHORS
            and median_author_score < NEGATIVE_SCORE
            and negative_count * 3 >= author_count * 2      # >= 2/3
        ),
        "is_account_dependent": bool(
            author_count >= ACCOUNT_DEPENDENT_MIN_AUTHORS
            and max_score >= POSITIVE_SCORE
            and min_score < NEGATIVE_SCORE
        ),
    }


def _build_value_item(
    value: str,
    display_value: str,
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    scores = [o["median_performance_score"] for o in observations]
    author_count = len(observations)
    positive = sum(1 for o in observations if o["direction"] == "positive")
    neutral = sum(1 for o in observations if o["direction"] == "neutral")
    negative = sum(1 for o in observations if o["direction"] == "negative")
    median_score = round(median(scores), 1)

    flags = classify_flags(
        author_count, median_score, positive, negative, min(scores), max(scores)
    )

    return {
        # identity：跨账号分组用（已规范化）
        "value": value,
        # 展示用：一个真实存在的原始拼写，绝不把 GPT 显示成 gpt
        "display_value": display_value,
        "author_count": author_count,
        # 只用于展示「证据量有多大」，**绝不**作为作者权重
        "total_sample_count": sum(o["sample_count"] for o in observations),
        "median_author_score": median_score,
        "positive_author_count": positive,
        "neutral_author_count": neutral,
        "negative_author_count": negative,
        "positive_rate": round(positive / author_count * 100, 1),
        "negative_rate": round(negative / author_count * 100, 1),
        "min_author_score": round(min(scores), 1),
        "max_author_score": round(max(scores), 1),
        "spread": round(max(scores) - min(scores), 1),
        **flags,
        "is_mixed": not any(flags.values()),
        # 按作者表现倒序：用户一眼能看出是谁拖后腿
        "authors": sorted(
            observations,
            key=lambda o: (-o["median_performance_score"], o["author_id"]),
        ),
    }


def _high_key(item: dict[str, Any]):
    """跨账号高表现排序：中位分 DESC → positive_rate DESC → 作者数 DESC → 取值。"""
    return (
        -item["median_author_score"],
        -item["positive_rate"],
        -item["author_count"],
        item["value"],
    )


def _low_key(item: dict[str, Any]):
    """跨账号低表现排序：中位分 ASC → negative_rate DESC → 作者数 DESC → 取值。"""
    return (
        item["median_author_score"],
        -item["negative_rate"],
        -item["author_count"],
        item["value"],
    )


def _spread_key(item: dict[str, Any]):
    """账号依赖：差异（max − min）最大的排前面。"""
    return (-item["spread"], -item["author_count"], item["value"])


def _all_key(item: dict[str, Any]):
    return (-item["median_author_score"], -item["author_count"], item["value"])


def _build_dimension(
    *,
    key: str,
    label: str,
    kind: str,
    field: str,
    authors: list[tuple[dict[str, Any], list[dict[str, Any]]]],
) -> dict[str, Any]:
    """跨账号聚合一个维度。**作者等权**：(author_id, normalized_value) 恒等于 1 条 observation。

    分组必须走「作者的实际样本」，不能走 Stage 8 的 raw value 聚合结果：
    同一作者若同时存在 GPT / gpt / " Gpt "，normalize 后都变成 gpt，
    只有按样本重新聚合才能保证这个作者仍然只投一票，
    且 sample_count / 中位分反映的是合并后的真实样本。
    """
    # norm -> {(author_id, content_id): sample}   ← 同一帖对同一 norm 只算一次
    posts: dict[str, dict[tuple[int, int], dict[str, Any]]] = {}
    # norm -> {(author_id, content_id): {raw spellings}}
    raws: dict[str, dict[tuple[int, int], set[str]]] = {}
    # norm -> {raw spelling: 帖子数}（用于选 display_value）
    spellings: dict[str, dict[str, int]] = {}

    for author, samples in authors:
        aid = int(author["id"])
        for sample in samples:
            payload = sample.get("analysis")
            if not payload:
                continue
            # 同一帖内的重复 raw 值先去重
            for raw in dict.fromkeys(repo.analysis_path_values(payload, field, kind)):
                norm = normalize_value(raw)
                if not norm:
                    continue
                k = (aid, int(sample["content_id"]))
                posts.setdefault(norm, {})[k] = sample
                raws.setdefault(norm, {}).setdefault(k, set()).add(raw)
                counts = spellings.setdefault(norm, {})
                counts[raw] = counts.get(raw, 0) + 1

    meta = {int(a["id"]): a for a, _ in authors}

    items: list[dict[str, Any]] = []
    for norm, post_map in posts.items():
        # 按作者归组：一个作者一组样本 → 一条 observation
        by_author: dict[int, list[dict[str, Any]]] = {}
        for (aid, _cid), sample in post_map.items():
            by_author.setdefault(aid, []).append(sample)

        observations: list[dict[str, Any]] = []
        for aid, samples in by_author.items():
            # 作者内部样本不足 → 该作者对此取值不形成有效判断
            if len(samples) < MIN_AUTHOR_SAMPLE:
                continue
            evidence_values = sorted(
                {r for (a_id, _c), rs in raws[norm].items() if a_id == aid for r in rs}
            )
            observations.append(
                _observation(meta[aid], samples, evidence_values)
            )
        if not observations:
            continue

        # display_value：样本最多的原始拼写；并列取字典序最小（确定性，不靠 AI）
        counts = spellings.get(norm) or {}
        display = (
            min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            if counts
            else norm
        )
        items.append(_build_value_item(norm, display, observations))

    items.sort(key=_all_key)

    return {
        "key": key,
        "label": label,
        "field": field,
        "kind": kind,
        "value_count": len(items),
        # 三块榜单按**独立 flag** 过滤：同一个 value 可以同时出现在多块
        "common_high": sorted(
            [i for i in items if i["is_common_high"]], key=_high_key
        )[:RANKING_LIMIT],
        "common_low": sorted(
            [i for i in items if i["is_common_low"]], key=_low_key
        )[:RANKING_LIMIT],
        "account_dependent": sorted(
            [i for i in items if i["is_account_dependent"]], key=_spread_key
        )[:RANKING_LIMIT],
        "all_values": items[:DIMENSION_VALUE_LIMIT],
    }


# ---------------------------------------------------------------- 次级互动指标（D9）

def _author_metric_medians(eligible: list[dict[str, Any]]) -> dict[str, float | None]:
    """单个作者的每个指标中位数。样本不足该指标→None（不是 0）。"""
    out: dict[str, float | None] = {}
    views = [s["view_count"] for s in eligible]
    out["view_count"] = round(median(views), 1) if views else None
    for _count_col, rate_key, _label in perf.SECONDARY_METRICS:
        values = [
            s["rates"][rate_key] for s in eligible if s["rates"].get(rate_key) is not None
        ]
        out[rate_key] = round(median(values), 6) if values else None
    return out


def engagement_dimensions(
    eligible_prepared: list[tuple[dict[str, Any], list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    """跨账号互动指标比较。

    沿用本模块的核心纪律：
      - **作者等权**：一个作者一票，先算该作者自己的中位比率，
        再对作者中位数取中位 —— 绝不把不同作者的帖子混在一起重算
        （否则发帖多的作者会用帖子数淹没别人）；
      - **作者内部最低样本量**：该作者在该指标上有值的帖子数 < MIN_AUTHOR_SAMPLE
        → 该作者不贡献 observation，只如实记录 sample_count；
      - 只给观测值，不给「谁更会写」的因果结论。
    """
    dimensions: list[dict[str, Any]] = []

    def _collect(metric: str, label: str, kind: str, pick) -> None:
        observations: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for author, eligible in eligible_prepared:
            picked = [pick(s) for s in eligible]
            values = [v for v in picked if v is not None]
            entry = {
                "author_id": int(author["id"]),
                "username": author.get("username"),
                "display_name": author.get("display_name"),
                "sample_count": len(values),
                "median_value": round(median(values), 6) if values else None,
            }
            if len(values) >= MIN_AUTHOR_SAMPLE:
                observations.append(entry)
            else:
                skipped.append(entry)

        author_medians = [o["median_value"] for o in observations]
        dimensions.append(
            {
                "metric": metric,
                "label": label,
                "kind": kind,
                "author_count": len(observations),
                "insufficient": len(observations) < ACCOUNT_DEPENDENT_MIN_AUTHORS,
                # 作者等权中位：先作者内中位，再对作者取中位
                "cross_author_median": (
                    round(median(author_medians), 6) if author_medians else None
                ),
                "author_observations": sorted(
                    observations, key=lambda o: (-(o["median_value"] or 0.0), o["author_id"])
                ),
                "insufficient_authors": skipped,
            }
        )

    _collect("view_count", "曝光（绝对值）", "absolute", lambda s: float(s["view_count"]))
    for _count_col, rate_key, label in perf.SECONDARY_METRICS:
        _collect(rate_key, label, "rate", lambda s, k=rate_key: s["rates"].get(k))
    return dimensions


# ---------------------------------------------------------------- 主入口

def _select_authors(
    conn: sqlite3.Connection, author_ids: list[int] | None
) -> list[dict[str, Any]]:
    """选择参与比较的作者。identity 一律是正式 author_id，不用 username。"""
    authors = repo.list_authors(conn, with_content=True)
    if not author_ids:
        return authors
    wanted = set(author_ids)
    selected = [a for a in authors if int(a["id"]) in wanted]
    found = {int(a["id"]) for a in selected}
    missing = sorted(wanted - found)
    if missing:
        raise ValueError(f"作者不存在或没有任何内容：{', '.join(str(i) for i in missing)}")
    return selected


def cross_account_insights(
    conn: sqlite3.Connection, author_ids: list[int] | None = None
) -> dict[str, Any]:
    """跨账号流量画像：确定性统计，0 AI，现算不缓存、不落库。

    每个作者**恰好一次**样本装载（Stage 8 的 load_samples，内部 1 次 JOIN），
    之后全部在内存里聚合，绝不按 dimension × value × author 反复查库。
    """
    authors = _select_authors(conn, author_ids)

    # (author, 该作者的 eligible 且已分析样本)
    prepared: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    # 次级指标比较用的是 eligible（不要求已分析）—— 互动效果与文本特征无关
    eligible_prepared: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    eligible_authors = 0
    analyzed_authors = 0
    author_rows: list[dict[str, Any]] = []

    for author in authors:
        eligible, summary = perf.load_samples(conn, int(author["id"]))
        analyzed = [s for s in eligible if s["has_analysis"]]
        if summary["eligible_count"] > 0:
            eligible_authors += 1
        if summary["analyzed_eligible_count"] > 0:
            analyzed_authors += 1
        prepared.append((author, analyzed))
        eligible_prepared.append((author, eligible))
        author_rows.append(
            {
                "author_id": int(author["id"]),
                "username": author.get("username"),
                "display_name": author.get("display_name"),
                "eligible_count": summary["eligible_count"],
                "analyzed_eligible_count": summary["analyzed_eligible_count"],
                "median_views": perf.baseline_stats(eligible)["median_views"],
                "metric_medians": _author_metric_medians(eligible),
            }
        )

    content_dimensions = [
        _build_dimension(
            key=key, label=label, kind=kind, field=key, authors=prepared
        )
        for key, label, kind in CONTENT_FIELDS
    ]
    writing_dimensions = [
        _build_dimension(
            key=field, label=label, kind=kind, field=field, authors=prepared
        )
        for field, label, kind in WRITING_FIELDS
    ]

    return {
        "author_summary": {
            "author_count": len(authors),
            "eligible_author_count": eligible_authors,
            "analyzed_author_count": analyzed_authors,
        },
        "authors": author_rows,
        "content_dimensions": content_dimensions,
        "writing_dimensions": writing_dimensions,
        # ---- D9：次级互动指标在跨账号层面同样可见 ----
        "engagement_dimensions": engagement_dimensions(eligible_prepared),
        # 恒定返回，供 UI 显示口径（不是 AI 生成的说明）
        "thresholds": {
            "min_author_sample": MIN_AUTHOR_SAMPLE,
            "common_pattern_min_authors": COMMON_PATTERN_MIN_AUTHORS,
            "account_dependent_min_authors": ACCOUNT_DEPENDENT_MIN_AUTHORS,
            "positive_score": POSITIVE_SCORE,
            "negative_score": NEGATIVE_SCORE,
        },
        "method": {
            "author_equal_weight": True,
            "comparison_basis": "作者内部的 Performance Score / median_performance_score",
            "topic_matching": "完全相同（trim + 空白标准化 + 大小写标准化），不做近义合并",
        },
    }
