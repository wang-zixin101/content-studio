"""单账号流量画像（阶段 8）：**确定性**历史表现分析。

全部是 SQL + Python 统计，**0 AI / 0 token**。
不做爆款预测、不做因果推断、不做跨账号比较、不进 WRITE。

------------------------------------------------------------------
两个样本池必须严格分开
------------------------------------------------------------------

A. performance eligible samples
   有有效 view_count + 非置顶 + 采集时已发布 >= 48h
   → 账号曝光基线、percentile、中位曝光、Top10% / Bottom40% 门槛
   **不要求已分析**

B. analyzed performance samples
   eligible **且** 有有效 ANALYSIS V2
   → topic / subtopic / content_type / 写法 / 二元组合
   没有 analysis 就不知道这条帖子属于什么特征，因此不能参与特征统计

------------------------------------------------------------------
与阶段 6 的分工（不可混淆）
------------------------------------------------------------------

阶段 6 写作画像回答「这个作者**经常**怎么写」。
阶段 8 流量画像回答「哪些写法**历史表现更好**」。
出现最多 ≠ 表现更好；本模块只给统计，不下因果结论。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from statistics import mean, median
from typing import Any

from app import repository as repo

# ---------------------------------------------------------------- 常量

ELIGIBLE_MIN_AGE_HOURS = 48      # 帖子必须已发布至少 48 小时
MIN_RANKING_SAMPLE = 5           # N < 5 → 不进入正式高/低表现榜
LOW_SAMPLE_THRESHOLD = 10        # 5 <= N < 10 → 可显示，但标记「样本较少」

TOP10_SCORE = 90.0               # Top 10% = score >= 90
BAND_A_SCORE = 75.0
BAND_B_SCORE = 40.0
BOTTOM40_SCORE = 40.0            # Bottom 40% = score < 40

HIGH_PERF_SCORE = 60.0           # 四象限：高表现
LOW_PERF_SCORE = 40.0            # 四象限：低表现

DIMENSION_TOP_VALUES = 50        # 单维度最多返回的取值数（控制 payload）
RANKING_LIMIT = 10               # 高/低表现榜各自最多多少条
COMBO_LIMIT = 10
EVIDENCE_PREVIEW = 5             # evidence 默认返回条数

COMBO_SEP = " × "


# ---------------------------------------------------------------- 时间解析

def _ensure_aware(value: datetime) -> datetime:
    """没有时区的 datetime 统一按 UTC 解释，避免 naive/aware 相减报错。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _parse_any(value: Any) -> datetime | None:
    """先按 X 的 RFC2822（Mon Oct 05 10:26:24 +0000 2026），再按 ISO8601。"""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        parsed = None
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return _ensure_aware(parsed)


def parse_posted_at(value: Any) -> datetime | None:
    """解析 X 原始发布时间（posted_at）。只负责解析，不改数据、不落库。"""
    return _parse_any(value)


def parse_captured_at(value: Any) -> datetime | None:
    """解析采集时间（captured_at，阶段 7B 保存的 ISO 时间）。"""
    return _parse_any(value)


def post_age_hours(posted: datetime, captured: datetime) -> float:
    return (captured - posted).total_seconds() / 3600.0


# ---------------------------------------------------------------- Performance Score

def performance_scores(view_counts: list[int]) -> list[float]:
    """0–100 百分位分数（**账号内部**排名，不可跨作者比较）。

    算法（确定性）：
      按 view_count 升序排；相同 view_count 取**平均 rank**；
      N > 1 : score = 100 * (avg_rank - 1) / (N - 1)
      N = 1 : score = 50
    结果：最低 0 / 中位 50 / 最高 100。无随机、无模型。
    """
    n = len(view_counts)
    if n == 0:
        return []
    if n == 1:
        return [50.0]

    order = sorted(range(n), key=lambda i: view_counts[i])
    scores = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and view_counts[order[j + 1]] == view_counts[order[i]]:
            j += 1
        # 1-based rank 区间 = [i+1, j+1]，取平均
        avg_rank = (i + j + 2) / 2.0
        score = round(100.0 * (avg_rank - 1) / (n - 1), 1)
        for k in range(i, j + 1):
            scores[order[k]] = score
        i = j + 1
    return scores


def performance_band(score: float) -> str:
    """账号内部历史分层。不是质量评级、不是爆款概率。"""
    if score >= TOP10_SCORE:
        return "S"
    if score >= BAND_A_SCORE:
        return "A"
    if score >= BAND_B_SCORE:
        return "B"
    return "C"


BAND_LABEL = {
    "S": "Top 10%",
    "A": "高表现",
    "B": "一般",
    "C": "低表现",
}


def _is_top10(score: float) -> bool:
    return score >= TOP10_SCORE


def _is_bottom40(score: float) -> bool:
    return score < BOTTOM40_SCORE


# ---------------------------------------------------------------- 表现指标

#: 次级互动指标：计数列 → 比率列 → 展示标签。
#
# 纪律（设计 §5「指标数据应怎样使用」）：
#   - 只做「计数 / 浏览量」的**比率**，不做加权合成，不产生「爆款总分」；
#   - 只有 view_count 存在且 > 0、且该计数不为 NULL 时才计算 ——
#     分母未知时比率无意义，NULL 表示未知，绝不当 0；
#   - 计数为 0 是「已知为 0」，比率就是 0.0，与 NULL 严格区分。
SECONDARY_METRICS: tuple[tuple[str, str, str], ...] = (
    ("favorite_count", "favorite_rate", "点赞率"),
    ("reply_count", "reply_rate", "评论率"),
    ("retweet_count", "retweet_rate", "转发率"),
    ("quote_count", "quote_rate", "引用率"),
    ("bookmark_count", "bookmark_rate", "收藏率"),
)

#: 书写目标 → (指标键, 标签)。默认目标是曝光，保持既有行为不变。
GOAL_METRICS: dict[str, tuple[str, str]] = {
    "exposure": ("view_count", "曝光"),
    "discussion": ("reply_rate", "讨论"),
    "spread": ("retweet_rate", "传播"),
    "saves": ("bookmark_rate", "收藏"),
}
DEFAULT_GOAL = "exposure"

GOAL_SAMPLES_LIMIT = 5          # 每个目标返回的高/低案例条数


def goal_metric_key(goal: str | None) -> str:
    """把书写目标映射成指标键；未知目标回落到曝光（默认口径）。"""
    return GOAL_METRICS.get(str(goal or DEFAULT_GOAL), GOAL_METRICS[DEFAULT_GOAL])[0]


def compute_rates(row: sqlite3.Row) -> dict[str, float | None]:
    """按行计算次级比率。分母不可用时为 None（不是 0）。"""
    views = row["view_count"]
    if views is None or int(views) <= 0:
        return {rate: None for _count, rate, _label in SECONDARY_METRICS}
    out: dict[str, float | None] = {}
    for count_col, rate_key, _label in SECONDARY_METRICS:
        value = row[count_col]
        out[rate_key] = None if value is None else round(int(value) / int(views), 6)
    return out


# ---------------------------------------------------------------- 样本装载

_ANALYSIS_FLAT_FIELDS = (
    "topic", "subtopic", "content_type", "core_message",
    "opening_type", "structure_type", "ending_type",
    "tone", "register", "directness", "certainty", "rhythm",
)


def _has_analysis(row: sqlite3.Row) -> bool:
    """是否有可用的 ANALYSIS 结果。

    口径与作者画像一致：analysis_state='analyzed'；此外这里额外要求确实有内容
    （analysis_json 或任一扁平字段有值），避免把空分析当成有效特征来源。
    """
    if row["analysis_state"] != "analyzed":
        return False
    if row["analysis_json"]:
        return True
    return any(row[key] for key in _ANALYSIS_FLAT_FIELDS)


def _excerpt(raw_text: str | None) -> str:
    return (raw_text or "").strip().replace("\n", " ")[:80]


def load_samples(
    conn: sqlite3.Connection, author_id: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """一次 JOIN 取全量 → 判定 eligible → 计算 score。

    返回 (eligible_samples, summary)。
    eligible_samples 每条都带：content_id / source / view_count / score /
    band / posted_at / captured_at / excerpt / analysis（dict 或 None）。

    被排除的帖子也如实计数，但**绝不**修改任何原始数据。
    """
    rows = repo.author_performance_samples(conn, author_id)
    total_content_count = len(rows)

    excluded = {
        "no_performance": 0,
        "no_view_count": 0,
        "pinned": 0,
        "too_fresh": 0,
        "invalid_time": 0,
        # 明确标注为非中文：不进中文账号的流量统计（缺失/und 不排除）
        "non_chinese": 0,
    }
    performance_count = 0

    candidates: list[dict[str, Any]] = []

    for row in rows:
        # 语言先判：被排除的帖子不应再进入任何后续计数
        if repo.is_non_chinese(row["source_meta"]):
            excluded["non_chinese"] += 1
            continue

        # captured_at 是 content_performance 的 NOT NULL 列：为 NULL 即无 performance 行
        if row["captured_at"] is None:
            excluded["no_performance"] += 1
            continue
        performance_count += 1

        view_count = row["view_count"]
        if view_count is None:
            excluded["no_view_count"] += 1
            continue
        view_count = int(view_count)

        if bool(row["is_pinned"]):
            excluded["pinned"] += 1
            continue

        # 时间必须先判定合法性，才能算年龄；因此 invalid_time 先于 too_fresh
        posted_dt = parse_posted_at(row["posted_at"])
        captured_dt = parse_captured_at(row["captured_at"])
        if posted_dt is None or captured_dt is None or captured_dt < posted_dt:
            excluded["invalid_time"] += 1
            continue

        if post_age_hours(posted_dt, captured_dt) < ELIGIBLE_MIN_AGE_HOURS:
            excluded["too_fresh"] += 1
            continue

        candidates.append(
            {
                "content_id": int(row["content_id"]),
                "source": row["source"],
                "raw_text": row["raw_text"] or "",
                "excerpt": _excerpt(row["raw_text"]),
                "posted_at": row["posted_at"],
                "captured_at": row["captured_at"],
                "view_count": view_count,
                "is_pinned": False,
                # 原始计数 + 比率：D9 要求点赞/评论/转发/引用/收藏真正进入统计，
                # 而不是只躺在 content_performance 里给详情页看。
                "counts": {
                    count_col: row[count_col]
                    for count_col, _rate, _label in SECONDARY_METRICS
                },
                "rates": compute_rates(row),
                "has_analysis": _has_analysis(row),
                "analysis": (
                    repo.analysis_payload(row) if _has_analysis(row) else None
                ),
            }
        )

    scores = performance_scores([c["view_count"] for c in candidates])
    for sample, score in zip(candidates, scores):
        sample["performance_score"] = score
        sample["band"] = performance_band(score)

    _attach_metric_scores(candidates)

    eligible = candidates
    analyzed_eligible = [s for s in eligible if s["has_analysis"]]

    summary = {
        "total_content_count": total_content_count,
        "performance_count": performance_count,
        "eligible_count": len(eligible),
        "analyzed_eligible_count": len(analyzed_eligible),
        "min_age_hours": ELIGIBLE_MIN_AGE_HOURS,
        "excluded": excluded,
    }
    return eligible, summary


def _attach_metric_scores(samples: list[dict[str, Any]]) -> None:
    """给每条样本挂上「账号内分位分」——每个指标各算一份。

    复用 performance_scores 的同一套算法（同值取平均 rank、N=1 → 50），
    因此不同指标之间口径一致、可比较，也不会引入新的打分体系。
    指标值缺失（None）的样本不参与该指标排名，其分位分为 None。
    """
    for _count_col, rate_key, _label in SECONDARY_METRICS:
        indexed = [
            (idx, s["rates"].get(rate_key))
            for idx, s in enumerate(samples)
        ]
        present = [(idx, v) for idx, v in indexed if v is not None]
        for idx, s in enumerate(samples):
            s.setdefault("metric_scores", {})[rate_key] = None
        if not present:
            continue
        values = [v for _idx, v in present]
        scored = performance_scores([float(v) for v in values])
        for (idx, _v), sc in zip(present, scored):
            samples[idx]["metric_scores"][rate_key] = sc
    # 曝光也用同一套分位分，方便统一读取（与 performance_score 同值）
    for s in samples:
        s.setdefault("metric_scores", {})["view_count"] = s["performance_score"]


# ---------------------------------------------------------------- Baseline

def baseline_stats(eligible: list[dict[str, Any]]) -> dict[str, Any]:
    """账号内部曝光基线。中位优先，平均值只作参考。"""
    if not eligible:
        return {
            "median_views": None,
            "average_views": None,
            "top10_view_threshold": None,
            "bottom40_view_threshold": None,
            "min_views": None,
            "max_views": None,
        }

    views = [s["view_count"] for s in eligible]
    top10_views = [s["view_count"] for s in eligible if _is_top10(s["performance_score"])]
    bottom40_views = [
        s["view_count"] for s in eligible if _is_bottom40(s["performance_score"])
    ]
    return {
        "median_views": round(median(views), 1),
        "average_views": round(mean(views), 1),
        "top10_view_threshold": min(top10_views) if top10_views else None,
        "bottom40_view_threshold": max(bottom40_views) if bottom40_views else None,
        "min_views": min(views),
        "max_views": max(views),
    }


# ---------------------------------------------------------------- 单维度聚合

def aggregate_samples(values: list[dict[str, Any]]) -> dict[str, Any]:
    """对同一取值下的样本集合算统计量。

    阶段 8（按 raw 取值分组）与阶段 9（按 normalized 取值分组，
    可能合并同一作者的多个 raw 变体）**共用**这一份实现，
    保证两边的 sample_count / 中位分 / Top10%·Bottom40% 口径永远一致。
    阶段 9 必须传「该作者在这个取值下的**实际样本**」，
    绝不能把多个 raw 变体的中位分再取一次中位。
    """
    n = len(values)
    scores = [s["performance_score"] for s in values]
    views = [s["view_count"] for s in values]
    top10 = sum(1 for s in scores if _is_top10(s))
    bottom40 = sum(1 for s in scores if _is_bottom40(s))
    return {
        "sample_count": n,
        "median_performance_score": round(median(scores), 1),
        "median_view_count": round(median(views), 1),
        "top10_count": top10,
        "top10_rate": round(top10 / n * 100, 1) if n else 0.0,
        "bottom40_count": bottom40,
        "bottom40_rate": round(bottom40 / n * 100, 1) if n else 0.0,
    }


def _high_key(item: dict[str, Any]):
    """高表现排序：中位分 DESC → top10_rate DESC → N DESC → 名称（保证稳定）。"""
    return (
        -item["median_performance_score"],
        -item["top10_rate"],
        -item["sample_count"],
        item["value"],
    )


def _low_key(item: dict[str, Any]):
    """低表现排序：中位分 ASC → bottom40_rate DESC → N DESC → 名称。"""
    return (
        item["median_performance_score"],
        -item["bottom40_rate"],
        -item["sample_count"],
        item["value"],
    )


def _assign_quadrants(items: list[dict[str, Any]]) -> None:
    """四象限辅助标签（只做标签，不做 Opportunity Score）。

    高频 = usage_rate >= 该 metric 内 N>=5 各取值的中位 usage_rate
    高表现 = 中位流量分 >= 60；低表现 = < 40；40–60 = 中性
    """
    ranked = [i for i in items if i["sample_count"] >= MIN_RANKING_SAMPLE]
    if not ranked:
        for item in items:
            item["quadrant"] = None
        return

    median_usage = median([i["usage_rate"] for i in ranked])
    for item in items:
        if item["sample_count"] < MIN_RANKING_SAMPLE:
            item["quadrant"] = None
            continue
        high_freq = item["usage_rate"] >= median_usage
        score = item["median_performance_score"]
        if score >= HIGH_PERF_SCORE:
            item["quadrant"] = "core" if high_freq else "opportunity"
        elif score < LOW_PERF_SCORE:
            item["quadrant"] = "high_freq_low" if high_freq else "low_priority"
        else:
            item["quadrant"] = "neutral"


def _build_dimension(
    *,
    key: str,
    label: str,
    samples: list[dict[str, Any]],
    field: str,
    kind: str = "categorical",
) -> dict[str, Any]:
    """按一个字段取值聚合（categorical：一篇对一个值贡献一次）。

    ------------------------------------------------------------------
    usage_rate 的分母必须是 **present_count**，不是 analyzed_eligible_count
    ------------------------------------------------------------------
    历史 ANALYSIS 可能缺 key / 为 null。字段缺失只能解释为「这条不知道」，
    不能解释成「作者没有这种写法」，否则缺失越多、所有取值的常用度越低。

    present_count = 该字段在该批样本中**实际拥有至少一个合法值**的 content 数
      - categorical：存在一个合法标量 → +1
      - list        ：列表至少有一个合法值 → +1
      - 空 list / null / missing → 不计入
    同一篇对同一个 value 仍然只贡献一次（去重不影响 present_count）。
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    present_ids: set[int] = set()
    for sample in samples:
        payload = sample.get("analysis")
        if not payload:
            continue
        raw_values = repo.analysis_path_values(payload, field, kind)
        if not raw_values:
            continue                              # 字段缺失：不进分母
        present_ids.add(int(sample["content_id"]))
        for value in dict.fromkeys(raw_values):   # 同一篇同值只贡献一次
            buckets.setdefault(value, []).append(sample)

    present_count = len(present_ids)

    items: list[dict[str, Any]] = []
    for value, group in buckets.items():
        stats = aggregate_samples(group)
        items.append(
            {
                "value": value,
                **stats,
                "usage_rate": (
                    round(len(group) / present_count * 100, 1) if present_count else 0.0
                ),
                "low_sample": (
                    MIN_RANKING_SAMPLE <= stats["sample_count"] < LOW_SAMPLE_THRESHOLD
                ),
            }
        )

    _assign_quadrants(items)

    all_sorted = sorted(items, key=_high_key)
    rankable = [i for i in items if i["sample_count"] >= MIN_RANKING_SAMPLE]
    return {
        "key": key,
        "label": label,
        "field": field,
        "kind": kind,
        # 字段真实覆盖率：缺字段的样本有多少，UI 与后续分析都要知道
        "present_count": present_count,
        "values": all_sorted[:DIMENSION_TOP_VALUES],
        "high": sorted(rankable, key=_high_key)[:RANKING_LIMIT],
        "low": sorted(rankable, key=_low_key)[:RANKING_LIMIT],
    }


# ---------------------------------------------------------------- 写法维度

# 阶段 6 已确认的真实 ANALYSIS V2 字段，直接复用，不新造写作 schema。
# 第一版刻意排除这 4 个「高维 / 长尾 / 作者特异」字段，
# 排除「长尾作者特异标签」类字段：它们取值高度分散，
# 大量碎片值会伪装成「流量密码」，但每个取值只有 1–2 条帖子支撑。
_EXCLUDED_WRITING_FIELDS = frozenset(
    {
        "style.distinctive_phrases",
    }
)

# 由阶段 6 的 WRITING_PROFILE_SECTIONS 派生 → 字段口径永远与阶段 6 一致
PERFORMANCE_WRITING_SECTIONS: list[dict[str, Any]] = [
    {
        "key": section["key"],
        "title": section["title"],
        "fields": [
            f
            for f in section["fields"]
            if f["path"] not in _EXCLUDED_WRITING_FIELDS
        ],
    }
    for section in repo.WRITING_PROFILE_SECTIONS
]
PERFORMANCE_WRITING_SECTIONS = [
    s for s in PERFORMANCE_WRITING_SECTIONS if s["fields"]
]

# 路径 → 字段定义（evidence 匹配用）
PERFORMANCE_WRITING_FIELDS: dict[str, dict[str, str]] = {
    f["path"]: f
    for section in PERFORMANCE_WRITING_SECTIONS
    for f in section["fields"]
}


# ---------------------------------------------------------------- 二元组合

COMBOS: list[dict[str, Any]] = [
    {
        "key": "topic_opening",
        "label": "主题 × 开头方式",
        "left": {"field": "topic", "label": "主题"},
        "right": {"field": "writing.opening_type", "label": "开头方式"},
    },
    {
        "key": "topic_structure",
        "label": "主题 × 整体结构",
        "left": {"field": "topic", "label": "主题"},
        "right": {"field": "writing.structure_type", "label": "整体结构"},
    },
    {
        "key": "content_type_opening",
        "label": "内容类型 × 开头方式",
        "left": {"field": "content_type", "label": "内容类型"},
        "right": {"field": "writing.opening_type", "label": "开头方式"},
    },
    {
        "key": "topic_tone",
        "label": "主题 × 语气",
        "left": {"field": "topic", "label": "主题"},
        "right": {"field": "style.tone", "label": "语气"},
    },
]

COMBOS_BY_KEY = {c["key"]: c for c in COMBOS}


def _categorical_value(payload: dict[str, Any], field: str) -> str | None:
    values = repo.analysis_path_values(payload, field, "categorical")
    return values[0] if values else None


def _build_combo(combo: dict[str, Any], samples: list[dict[str, Any]]) -> dict[str, Any]:
    """二元组合聚合。usage_rate 分母 = **两侧都取到值**的样本数（同单维度口径）。"""
    left_field = combo["left"]["field"]
    right_field = combo["right"]["field"]
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    present_ids: set[int] = set()

    for sample in samples:
        payload = sample.get("analysis")
        if not payload:
            continue
        left = _categorical_value(payload, left_field)
        right = _categorical_value(payload, right_field)
        if left is None or right is None:
            continue                              # 任一侧缺失 → 不进分母
        present_ids.add(int(sample["content_id"]))
        buckets.setdefault((left, right), []).append(sample)

    present_count = len(present_ids)

    items: list[dict[str, Any]] = []
    for (left, right), group in buckets.items():
        stats = aggregate_samples(group)
        items.append(
            {
                # value 只用于展示；identity 一律走 left_value / right_value
                "value": f"{left}{COMBO_SEP}{right}",
                "left_value": left,
                "right_value": right,
                **stats,
                "usage_rate": (
                    round(len(group) / present_count * 100, 1) if present_count else 0.0
                ),
                "low_sample": (
                    MIN_RANKING_SAMPLE <= stats["sample_count"] < LOW_SAMPLE_THRESHOLD
                ),
            }
        )

    _assign_quadrants(items)
    rankable = [i for i in items if i["sample_count"] >= MIN_RANKING_SAMPLE]
    return {
        "key": combo["key"],
        "label": combo["label"],
        "left": combo["left"],
        "right": combo["right"],
        "present_count": present_count,
        "high": sorted(rankable, key=_high_key)[:COMBO_LIMIT],
        "low": sorted(rankable, key=_low_key)[:COMBO_LIMIT],
    }


# ---------------------------------------------------------------- 次级指标（D9）
#
# 设计 §5：「针对点赞、评论、转发、引用、收藏，增加独立的计数/浏览比率，
# 可用于寻找不同目标的案例：曝光、讨论、传播、收藏，不合成一个主观爆款总分。」
#
# 因此这里做两件事，且**只做这两件**：
#   1. metric_baselines：每个指标在账号内的中位数 / Top10 / Bottom40 门槛；
#   2. goal_samples    ：每个目标下的高/低案例（真实帖子，可点开）。
# 全部是确定性统计，0 AI；样本条件沿用 eligible（≥48h、非置顶、有 view_count）。


def _metric_value(sample: dict[str, Any], metric: str) -> float | None:
    if metric == "view_count":
        return float(sample["view_count"])
    return sample["rates"].get(metric)


def metric_baselines(eligible: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """每个指标的账号内基线。样本不足时如实标 sample_count，不编造阈值。"""
    out: list[dict[str, Any]] = []

    views = [s["view_count"] for s in eligible]
    base = baseline_stats(eligible)
    out.append(
        {
            "metric": "view_count",
            "label": "曝光",
            "kind": "absolute",
            "sample_count": len(views),
            # 复用 baseline_stats 的同一份口径，避免两处各算一遍导致漂移
            "median_value": base["median_views"],
            "min_value": base["min_views"],
            "max_value": base["max_views"],
            "top10_threshold": base["top10_view_threshold"],
            "bottom40_threshold": base["bottom40_view_threshold"],
            # 曝光是 eligible 的准入条件，因此不存在未知值
            "unknown_count": 0,
        }
    )

    for _count_col, rate_key, label in SECONDARY_METRICS:
        values = [
            s["rates"][rate_key] for s in eligible if s["rates"].get(rate_key) is not None
        ]
        scores = [
            s["metric_scores"].get(rate_key)
            for s in eligible
            if s["metric_scores"].get(rate_key) is not None
        ]
        out.append(
            {
                "metric": rate_key,
                "label": label,
                "kind": "rate",
                "sample_count": len(values),
                # 比率按 1e-6 精度存储，展示时保留 6 位小数即可无损
                "median_value": round(median(values), 6) if values else None,
                "min_value": round(min(values), 6) if values else None,
                "max_value": round(max(values), 6) if values else None,
                "top10_threshold": _score_threshold(values, scores, top=True),
                "bottom40_threshold": _score_threshold(values, scores, top=False),
                # 浏览量为 0 或未知 → 比率不可算。这是「不知道」，不是「效果差」
                "unknown_count": sum(
                    1 for s in eligible if s["rates"].get(rate_key) is None
                ),
            }
        )
    return out


def _score_threshold(
    values: list[float], scores: list[float], *, top: bool
) -> float | None:
    """按「该指标自己的分位分」取出阈值。

    次级指标没有天然的绝对门槛（一条 3% 点赞率算高还是低取决于账号本身），
    因此用指标内分位分划分才算可比。

    top=True  → Top10 组里的**最小值**（达到它就算 Top10）
    top=False → Bottom40 组里的**最大值**（低于它就算 Bottom40）
    """
    picked = [
        v
        for v, sc in zip(values, scores)
        if sc is not None and (_is_top10(sc) if top else _is_bottom40(sc))
    ]
    if not picked:
        return None
    return round(min(picked) if top else max(picked), 6)


def goal_samples(eligible: list[dict[str, Any]]) -> dict[str, Any]:
    """按书写目标列出高/低案例。

    只挑**该指标有值**的帖子：比率算不出来的帖子不能被说成「讨论效果差」——
    那只是「不知道」。样本不足时返回空榜，不凑数。
    """
    out: dict[str, Any] = {}
    for goal, (metric, label) in GOAL_METRICS.items():
        pool = [s for s in eligible if _metric_value(s, metric) is not None]
        if len(pool) < MIN_RANKING_SAMPLE:
            out[goal] = {
                "goal": goal,
                "label": label,
                "metric": metric,
                "sample_count": len(pool),
                "insufficient": True,
                "high": [],
                "low": [],
            }
            continue
        ordered = sorted(
            pool, key=lambda s: (-(_metric_value(s, metric) or 0.0), s["content_id"])
        )
        out[goal] = {
            "goal": goal,
            "label": label,
            "metric": metric,
            "sample_count": len(pool),
            "insufficient": False,
            "high": [_goal_item(s, metric) for s in ordered[:GOAL_SAMPLES_LIMIT]],
            "low": [_goal_item(s, metric) for s in ordered[-GOAL_SAMPLES_LIMIT:][::-1]],
        }
    return out


def _goal_item(sample: dict[str, Any], metric: str) -> dict[str, Any]:
    return {
        "content_id": sample["content_id"],
        "source": sample["source"],
        "excerpt": sample["excerpt"],
        "posted_at": sample["posted_at"],
        "view_count": sample["view_count"],
        "metric": metric,
        "metric_value": _metric_value(sample, metric),
        "metric_score": sample["metric_scores"].get(metric),
        "performance_score": sample["performance_score"],
        "band": sample["band"],
        # 该帖的可观察写法特征（只取结构化取值，不带原文，避免被照抄）
        "features": _feature_summary(sample),
    }


#: 案例帖展示给「写作参照」的结构化特征路径
_GOAL_FEATURE_PATHS: tuple[tuple[str, str], ...] = (
    ("writing.opening_type", "opening"),
    ("writing.structure_type", "structure"),
    ("writing.ending_type", "ending"),
    ("style.tone", "tone"),
    ("style.directness", "directness"),
    ("style.rhythm", "rhythm"),
)


def _feature_summary(sample: dict[str, Any]) -> dict[str, Any]:
    """案例帖的写法特征摘要。没有分析结果 → 空 dict（不是编造）。"""
    payload = sample.get("analysis")
    if not isinstance(payload, dict):
        return {}
    out: dict[str, Any] = {}
    for path, key in _GOAL_FEATURE_PATHS:
        values = repo.analysis_path_values(payload, path, "categorical")
        if values:
            out[key] = values[0]
    for path, key in (
        ("writing.moves[].type", "moves"),
        ("style.markers[].type", "markers"),
    ):
        values = repo.analysis_path_values(payload, path, "list")
        if values:
            # 只保留出现顺序前若干个，控制 payload
            out[key] = values[:6]
    return out


# ---------------------------------------------------------------- 主入口

CONTENT_DIMENSIONS = (
    ("topic", "主题", "topic"),
    ("subtopic", "子主题", "subtopic"),
    ("content_type", "内容类型", "content_type"),
)


def author_performance_profile(
    conn: sqlite3.Connection, author_id: int
) -> dict[str, Any]:
    """单账号流量画像：确定性统计，0 AI，现算不缓存、不落库。"""
    eligible, summary = load_samples(conn, author_id)
    analyzed = [s for s in eligible if s["has_analysis"]]

    baseline = baseline_stats(eligible)

    content_dimensions = [
        _build_dimension(
            key=key, label=label, samples=analyzed, field=field,
        )
        for key, label, field in CONTENT_DIMENSIONS
    ]

    writing_sections: list[dict[str, Any]] = []
    for section in PERFORMANCE_WRITING_SECTIONS:
        metrics = [
            _build_dimension(
                key=f["path"], label=f["label"], samples=analyzed,
                field=f["path"], kind=f["kind"],
            )
            for f in section["fields"]
        ]
        writing_sections.append(
            {"key": section["key"], "title": section["title"], "metrics": metrics}
        )

    combinations = [_build_combo(combo, analyzed) for combo in COMBOS]

    return {
        "author_id": int(author_id),
        "sample_summary": summary,
        "baseline": baseline,
        "content_dimensions": content_dimensions,
        "writing_sections": writing_sections,
        "combinations": combinations,
        # ---- D9：次级互动指标真正进入画像 ----
        "metric_baselines": metric_baselines(eligible),
        "goal_samples": goal_samples(eligible),
        # 恒定返回，供 UI 显示口径说明（不是 AI 生成的总结）
        "thresholds": {
            "min_age_hours": ELIGIBLE_MIN_AGE_HOURS,
            "min_ranking_sample": MIN_RANKING_SAMPLE,
            "low_sample_threshold": LOW_SAMPLE_THRESHOLD,
            "top10_score": TOP10_SCORE,
            "bottom40_score": BOTTOM40_SCORE,
        },
        "goals": {
            goal: {"label": label, "metric": metric}
            for goal, (metric, label) in GOAL_METRICS.items()
        },
        "default_goal": DEFAULT_GOAL,
    }


# ---------------------------------------------------------------- Evidence

def _evidence_item(sample: dict[str, Any], matched_value: str) -> dict[str, Any]:
    return {
        "content_id": sample["content_id"],
        "source": sample["source"],
        "excerpt": sample["excerpt"],
        "posted_at": sample["posted_at"],
        "view_count": sample["view_count"],
        "performance_score": sample["performance_score"],
        "band": sample["band"],
        "band_label": BAND_LABEL.get(sample["band"], sample["band"]),
        "matched_value": matched_value,
    }


EVIDENCE_ORDER_HIGH = "high"
EVIDENCE_ORDER_LOW = "low"
EVIDENCE_ORDERS = (EVIDENCE_ORDER_HIGH, EVIDENCE_ORDER_LOW)


def _sort_evidence(matched: list[dict[str, Any]], order: str) -> None:
    """证据排序。默认 high（先看最好的），低表现榜必须传 low（先看最差的）。

    低表现类别里最好的帖子并不能说明「这个写法为什么差」，
    所以 UI 点开低表现项时必须先看到真正低的样本。
    """
    if order == EVIDENCE_ORDER_HIGH:
        matched.sort(key=lambda i: (-i["performance_score"], -i["content_id"]))
    else:                                   # low
        matched.sort(key=lambda i: (i["performance_score"], i["content_id"]))


def performance_evidence(
    conn: sqlite3.Connection,
    author_id: int,
    *,
    dimension: str | None = None,
    combo: str | None = None,
    value: str | None = None,
    left_value: str | None = None,
    right_value: str | None = None,
    order: str = EVIDENCE_ORDER_HIGH,
    limit: int = EVIDENCE_PREVIEW,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """某个取值 / 组合的真实证据帖（**精确匹配**，绝不随机挑代表帖）。

    单维度：
      dimension = topic / subtopic / content_type / 任一写作字段路径
      value     = 目标取值
    二元组合（**结构化 identity，不解析展示字符串**）：
      combo      = topic_opening / topic_structure / content_type_opening / topic_tone
      left_value = 左侧取值，right_value = 右侧取值

    组合的展示文本仍然是「A × B」，但**绝不**用 split(" × ") 反解数据：
    取值本身完全可能含有「 × 」（例如主题 "GPT × Claude"）。

    order = high（默认，表现分倒序） / low（表现分升序）
    """
    if order not in EVIDENCE_ORDERS:
        raise ValueError(f"未知的排序方式：{order}（可选 high / low）")
    if combo and combo not in COMBOS_BY_KEY:
        raise ValueError(f"未知的组合类型：{combo}")
    if not combo and dimension is None:
        raise ValueError("必须提供 dimension 或 combo")

    eligible, _summary = load_samples(conn, author_id)
    # 特征类证据必须要求 analysis：没有 analysis 就谈不上「属于这个特征」
    analyzed = [s for s in eligible if s["has_analysis"]]

    matched: list[dict[str, Any]] = []

    if combo:
        spec = COMBOS_BY_KEY[combo]
        if left_value is None or right_value is None:
            raise ValueError("组合证据必须同时提供 left_value 与 right_value")
        left = left_value.strip()
        right = right_value.strip()
        if not left or not right:
            raise ValueError("组合的 left_value / right_value 不得为空")
        display = f"{left}{COMBO_SEP}{right}"
        for sample in analyzed:
            payload = sample["analysis"]
            if (
                _categorical_value(payload, spec["left"]["field"]) == left
                and _categorical_value(payload, spec["right"]["field"]) == right
            ):
                matched.append(_evidence_item(sample, display))
    else:
        target = (value or "").strip()
        if not target:
            raise ValueError("单维度证据必须提供 value")
        if dimension in PERFORMANCE_WRITING_FIELDS:
            field_def = PERFORMANCE_WRITING_FIELDS[dimension]
            for sample in analyzed:
                values = repo.analysis_path_values(
                    sample["analysis"], dimension, field_def["kind"]
                )
                if target in values:
                    matched.append(_evidence_item(sample, target))
        else:
            allowed = {"topic", "subtopic", "content_type"}
            if dimension not in allowed:
                raise ValueError(f"未知的画像维度：{dimension}")
            for sample in analyzed:
                if _categorical_value(sample["analysis"], dimension) == target:
                    matched.append(_evidence_item(sample, target))

    # 同一 content_id 只可能出现一次：load_samples 按 content 去重装载
    _sort_evidence(matched, order)
    total = len(matched)
    return matched[offset : offset + limit], total
