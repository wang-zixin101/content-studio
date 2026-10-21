"""Rewrite Context Selector（阶段 10）：从已有画像里确定性挑少量高价值规则。

------------------------------------------------------------------
0 AI / 0 token
------------------------------------------------------------------
本模块只做确定性的「选择 + 截断 + 结构化」：

    读已经算好的 Stage 6 / 8 / 9 结果
    → 按固定优先级挑少量规则
    → 输出一个紧凑 dict 给 WRITE prompt

绝不：
  - 重新分析任何内容
  - 为了挑策略再调一次 AI
  - 读取 evidence 原帖做 RAG
  - 把整个 Stage 8 / Stage 9 JSON 原样塞进 prompt

------------------------------------------------------------------
缺什么跳什么（阶段 10 与阶段 7A 的关键差异）
------------------------------------------------------------------
Stage 7A 的语义是「参考作者 0 篇已分析 → 明确报错」。
阶段 10 目标是**高产**，因此任何一层缺失都只是**跳过**：

    Writing Profile 无      → 跳过
    Performance Profile 无  → 跳过
    Cross Insights 空       → 跳过

绝不 400、绝不要求先分析、绝不自动分析。
缺失的层会如实记录到 skipped[]，供 UI 显示 ✓ / —。

------------------------------------------------------------------
Context Budget（硬上限，测试会锁死）
------------------------------------------------------------------
    Writing Profile        ≤ 8 个 metric
    Performance Profile    ≤ 3 高 + 2 低
    Cross-account          ≤ 3 高 + 2 低
    account_dependent      ≤ 2 条，且**仅在有参考作者时**才允许出现
"""

from __future__ import annotations

from typing import Any

from app.services import performance_profile as perf

# ---------------------------------------------------------------- 预算

MAX_WRITING_METRICS = 8
MAX_PERFORMANCE_HIGH = 3
MAX_PERFORMANCE_LOW = 2
MAX_CROSS_HIGH = 3
MAX_CROSS_LOW = 2
MAX_ACCOUNT_DEPENDENT = 2
#: 书写目标对应的案例条数（高 / 低各这么多）—— 只带结构化特征，不带原文
MAX_GOAL_CASES = 3

# 沿用 Stage 8 的四象限口径
POSITIVE_SCORE = 60.0
NEGATIVE_SCORE = 40.0

# ---------------------------------------------------------------- Writing 优先级
#
# 只挑「高信号」写法字段：开头、结构、抓取机制、修辞动作、语气、句式、直接程度、语体/节奏。
# **刻意不优先** distinctive_phrases / 各类 marker —— 那些是长尾作者特异标签，
# 塞进 prompt 只会把候选稿带偏成模仿口头禅（Stage 7 已经明确禁止照抄）。
# 列表顺序即优先级；只取真实有取值的前 N 个。
WRITING_PRIORITY: tuple[str, ...] = (
    "writing.opening_type",
    "writing.structure_type",
    "writing.moves[].type",
    "writing.ending_type",
    "style.tone",
    "style.register",
    "style.directness",
    "style.certainty",
    "style.rhythm",
    "style.markers[].type",
)

# ---------------------------------------------------------------- Performance 优先级
#
# 素材已有 ANALYSIS 时，优先挑与当前 topic / content_type 相关的维度，
# 再补 opening / structure / tone / moves。没有 ANALYSIS 就按整体排名取 Top N。
PERFORMANCE_PRIORITY_FIELDS: tuple[str, ...] = (
    "topic",
    "content_type",
    "writing.opening_type",
    "writing.structure_type",
    "writing.moves[].type",
    "style.tone",
    "style.directness",
    "style.rhythm",
    "writing.ending_type",
)

# Cross-account 只认 Stage 9 真正铺开的维度（与 cross_account.WRITING_FIELDS 一致）
CROSS_PRIORITY_KEYS: tuple[str, ...] = (
    "topic",
    "content_type",
    "writing.opening_type",
    "writing.structure_type",
    "writing.moves[].type",
    "writing.ending_type",
    "style.tone",
    "style.register",
    "style.directness",
    "style.certainty",
    "style.rhythm",
    "style.markers[].type",
)

# source analysis 只取这些**真实已有**的字段，绝不把整个对象塞进来
# （完整对象由 prompts 的组装器单独注入，这里只是给策略选择用的紧凑摘要）
SOURCE_ANALYSIS_FIELDS: tuple[str, ...] = (
    "topic",
    "subtopic",
    "content_type",
    "core_message",
    "writing.opening_type",
    "writing.structure_type",
    "writing.ending_type",
    "style.tone",
    "style.register",
    "style.directness",
    "style.certainty",
    "style.rhythm",
)


# ---------------------------------------------------------------- 小工具

def _metric_map(writing_profile: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """把 Stage 6 画像拍平成 {field_path: {label, values}}。"""
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(writing_profile, dict):
        return out
    for section in writing_profile.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for metric in section.get("metrics") or []:
            if not isinstance(metric, dict):
                continue
            path = metric.get("field")
            if not path:
                continue
            values = [
                {"value": v.get("value"), "pct": v.get("pct")}
                for v in (metric.get("values") or [])[:3]
                if isinstance(v, dict) and v.get("value")
            ]
            if values:
                out[str(path)] = {"label": metric.get("label") or path, "values": values}
    return out


def _perf_dimensions(performance_profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    """把 Stage 8 画像拍平成 [{field, label, high[], low[]}]。"""
    if not isinstance(performance_profile, dict):
        return []
    dims: list[dict[str, Any]] = []
    for dim in performance_profile.get("content_dimensions") or []:
        if isinstance(dim, dict):
            dims.append(dim)
    for section in performance_profile.get("writing_sections") or []:
        if not isinstance(section, dict):
            continue
        for metric in section.get("metrics") or []:
            if isinstance(metric, dict):
                dims.append(metric)
    return dims


def _dim_field(dim: dict[str, Any]) -> str:
    return str(dim.get("field") or dim.get("key") or "")


def _perf_item(dim: dict[str, Any], item: dict[str, Any], bucket: str) -> dict[str, Any]:
    """只保留 prompt 需要的字段：绝不输出 evidence、不输出 usage_rate 等展示字段。"""
    return {
        "dimension": _dim_field(dim),
        "label": dim.get("label"),
        "value": item.get("value"),
        "direction": bucket,                       # high / low
        "median_performance_score": item.get("median_performance_score"),
        "sample_count": item.get("sample_count"),
    }


def _cross_item(dim: dict[str, Any], item: dict[str, Any], bucket: str) -> dict[str, Any]:
    return {
        "dimension": dim.get("key"),
        "label": dim.get("label"),
        "value": item.get("display_value") or item.get("value"),
        "direction": bucket,                       # high / low
        "median_author_score": item.get("median_author_score"),
        "author_count": item.get("author_count"),
    }


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


# ---------------------------------------------------------------- 各层选择

def select_writing_context(
    writing_profile: dict[str, Any] | None,
    *,
    limit: int = MAX_WRITING_METRICS,
) -> list[dict[str, Any]]:
    """挑最多 limit 个高信号写法 metric。只传 Top3 取值 + 占比，不传证据帖。"""
    metrics = _metric_map(writing_profile)
    out: list[dict[str, Any]] = []
    for path in WRITING_PRIORITY:
        if len(out) >= limit:
            break
        metric = metrics.get(path)
        if metric:
            out.append({"field": path, **metric})
    return out


def select_performance_context(
    performance_profile: dict[str, Any] | None,
    *,
    source_analysis: dict[str, Any] | None = None,
    high_limit: int = MAX_PERFORMANCE_HIGH,
    low_limit: int = MAX_PERFORMANCE_LOW,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """挑最多 3 高 + 2 低表现规律。

    素材已有 ANALYSIS → 优先与当前 topic / content_type / opening / structure 相关的取值；
    否则按该作者整体排名取 Top N。
    """
    dims = _perf_dimensions(performance_profile)
    if not dims:
        return [], []

    wanted: dict[str, str] = {}
    if isinstance(source_analysis, dict):
        for field in ("topic", "content_type"):
            value = source_analysis.get(field)
            if value:
                wanted[str(field)] = _norm(value)

    def score_of(item: dict[str, Any]) -> float:
        try:
            return float(item.get("median_performance_score") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def sample_of(item: dict[str, Any]) -> int:
        try:
            return int(item.get("sample_count") or 0)
        except (TypeError, ValueError):
            return 0

    matched_high: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    matched_low: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    rest_high: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    rest_low: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    for dim in dims:
        field = _dim_field(dim)
        target = wanted.get(field)
        # Stage 8 的 high / low 只是「该维度里中位最高/最低的前 N 个取值」，
        # **不是**「高表现 / 低表现」——单一取值维度会把同一个值同时放进两榜，
        # 且中位 50 的值也会出现在 low 里。
        # 因此这里必须再用 60 / 40 门槛过滤一次，否则会输出「低表现：高效主题」这种自相矛盾的规律。
        for item in dim.get("high") or []:
            if not isinstance(item, dict) or sample_of(item) <= 0:
                continue
            if score_of(item) < POSITIVE_SCORE:
                continue
            hit = target is not None and _norm(item.get("value")) == target
            (matched_high if hit else rest_high).append((field, dim, item))
        for item in dim.get("low") or []:
            if not isinstance(item, dict) or sample_of(item) <= 0:
                continue
            if score_of(item) >= NEGATIVE_SCORE:
                continue
            hit = target is not None and _norm(item.get("value")) == target
            (matched_low if hit else rest_low).append((field, dim, item))

    def rank(rows: list[tuple[str, dict[str, Any], dict[str, Any]]], desc: bool):
        order = {f: i for i, f in enumerate(PERFORMANCE_PRIORITY_FIELDS)}
        return sorted(
            rows,
            key=lambda r: (
                -score_of(r[2]) if desc else score_of(r[2]),
                order.get(r[0], len(order)),
                -sample_of(r[2]),
                str(r[2].get("value") or ""),
            ),
        )

    high_rows = rank(matched_high, True) + rank(rest_high, True)
    low_rows = rank(matched_low, False) + rank(rest_low, False)

    high = [_perf_item(d, i, "high") for _f, d, i in high_rows[:high_limit]]
    low = [_perf_item(d, i, "low") for _f, d, i in low_rows[:low_limit]]
    return high, low


def select_cross_context(
    cross_insights: dict[str, Any] | None,
    *,
    high_limit: int = MAX_CROSS_HIGH,
    low_limit: int = MAX_CROSS_LOW,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """挑最多 3 条 common_high + 2 条 common_low（不需要参考作者，none 也可用）。"""
    if not isinstance(cross_insights, dict):
        return [], []

    dims: list[dict[str, Any]] = []
    for group in ("content_dimensions", "writing_dimensions"):
        for dim in cross_insights.get(group) or []:
            if isinstance(dim, dict):
                dims.append(dim)

    order = {k: i for i, k in enumerate(CROSS_PRIORITY_KEYS)}
    dims.sort(key=lambda d: order.get(str(d.get("key")), len(order)))

    def rank(items: list[dict[str, Any]], desc: bool):
        rows = [i for i in items if isinstance(i, dict)]
        return sorted(
            rows,
            key=lambda i: (
                -(i.get("median_author_score") or 0) if desc
                else (i.get("median_author_score") or 0),
                -(i.get("author_count") or 0),
                str(i.get("display_value") or i.get("value") or ""),
            ),
        )

    high: list[dict[str, Any]] = []
    low: list[dict[str, Any]] = []
    for dim in dims:
        high.extend(
            _cross_item(dim, i, "high")
            for i in rank(dim.get("common_high") or [], True)[:high_limit]
        )
        low.extend(
            _cross_item(dim, i, "low")
            for i in rank(dim.get("common_low") or [], False)[:low_limit]
        )

    return high[:high_limit], low[:low_limit]


def select_account_dependent_context(
    cross_insights: dict[str, Any] | None,
    *,
    reference_author_id: int | None,
    limit: int = MAX_ACCOUNT_DEPENDENT,
) -> list[dict[str, Any]]:
    """account_dependent：**只有存在参考作者时才允许使用**，且方向由代码判定。

    Stage 9 只说「跨账号差异明显」，模型自己判断不了该不该用 —— 代码必须提前定方向：
      - 参考作者在该取值上的中位分 >= 60 → favor（可作为候选结构）
      - < 40                            → avoid（本次避免作为主要结构）
      - 40–60 或该作者没有这条 observation → 跳过（不传，避免噪音）

    参考作者 = None → 直接返回空，绝不传。
    """
    if reference_author_id is None or not isinstance(cross_insights, dict):
        return []

    dims: list[dict[str, Any]] = []
    for group in ("content_dimensions", "writing_dimensions"):
        for dim in cross_insights.get(group) or []:
            if isinstance(dim, dict):
                dims.append(dim)

    order = {k: i for i, k in enumerate(CROSS_PRIORITY_KEYS)}
    dims.sort(key=lambda d: order.get(str(d.get("key")), len(order)))

    rows: list[dict[str, Any]] = []
    for dim in dims:
        for item in dim.get("account_dependent") or []:
            if not isinstance(item, dict):
                continue
            score = _reference_score(item, reference_author_id)
            if score is None:
                continue
            if score >= POSITIVE_SCORE:
                stance = "favor"
            elif score < NEGATIVE_SCORE:
                stance = "avoid"
            else:
                continue                      # 中性 → 不确定，跳过
            rows.append(
                {
                    "dimension": dim.get("key"),
                    "label": dim.get("label"),
                    "value": item.get("display_value") or item.get("value"),
                    "reference_stance": stance,
                    "reference_author_score": score,
                    "cross_median_author_score": item.get("median_author_score"),
                    "spread": item.get("spread"),
                }
            )

    rows.sort(
        key=lambda r: (
            -(r.get("spread") or 0),
            -(r.get("reference_author_score") or 0),
            str(r.get("value") or ""),
        )
    )
    return rows[:limit]


def _reference_score(item: dict[str, Any], reference_author_id: int) -> float | None:
    """取参考作者在这条 account_dependent 规律上的**自己**的中位分。"""
    for obs in item.get("authors") or []:
        if not isinstance(obs, dict):
            continue
        try:
            if int(obs.get("author_id")) != int(reference_author_id):
                continue
        except (TypeError, ValueError):
            continue
        try:
            return float(obs.get("median_performance_score"))
        except (TypeError, ValueError):
            return None
    return None


def _read_path(payload: dict[str, Any], path: str) -> Any:
    """按点分路径读嵌套字段；任一层缺失就返回 None。

    新版合同里 source analysis 有 `writing.opening_type` 这样的嵌套路径，
    直接 `.get("writing.opening_type")` 永远取不到值 —— 那会让精选来源
    在 prompt 里**静默变空**，正是本阶段要消灭的那一类缺陷。
    """
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def select_source_analysis(
    source_analysis: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """素材已有 ANALYSIS → 只取少量真实字段；没有 → None（直接跳过）。"""
    if not isinstance(source_analysis, dict):
        return None
    out: dict[str, Any] = {}
    for field in SOURCE_ANALYSIS_FIELDS:
        value = _read_path(source_analysis, field)
        if value not in (None, "", []):
            out[field] = value
    return out or None


def select_goal_context(
    performance_profile: dict[str, Any] | None,
    *,
    goal: str | None,
    cases_limit: int = MAX_GOAL_CASES,
) -> dict[str, Any] | None:
    """按**当次书写目标**取该指标的基线与案例特征（D9 的「写作参照」一环）。

    - 目标未知 / 缺数据 → None（调用方按「跳过」处理，绝不编造）；
    - 案例只取**结构化写法特征**与指标值，不取原文 ——
      否则候选稿会朝「照抄参考帖」的方向漂移；
    - 默认目标仍是曝光，因此老调用方的行为不变。
    """
    if not isinstance(performance_profile, dict):
        return None

    metric = perf.goal_metric_key(goal)
    resolved_goal = goal if goal in perf.GOAL_METRICS else perf.DEFAULT_GOAL

    baseline = None
    for row in performance_profile.get("metric_baselines") or []:
        if isinstance(row, dict) and row.get("metric") == metric:
            baseline = {
                "sample_count": row.get("sample_count"),
                "median_value": row.get("median_value"),
                "top10_threshold": row.get("top10_threshold"),
                "bottom40_threshold": row.get("bottom40_threshold"),
                "unknown_count": row.get("unknown_count"),
            }
            break

    block = (performance_profile.get("goal_samples") or {}).get(resolved_goal) or {}
    if not baseline and not block:
        return None
    if block.get("insufficient") and not baseline:
        return None

    def _case(item: dict[str, Any]) -> dict[str, Any]:
        out = {
            "metric_value": item.get("metric_value"),
            "metric_score": item.get("metric_score"),
            "view_count": item.get("view_count"),
        }
        features = item.get("features") or {}
        if features:
            out["features"] = features
        return out

    label = (perf.GOAL_METRICS.get(resolved_goal) or ("", ""))[1]
    return {
        "goal": resolved_goal,
        "label": label,
        "metric": metric,
        "metric_kind": "absolute" if metric == "view_count" else "rate",
        "baseline": baseline,
        "sample_count": block.get("sample_count"),
        "insufficient": bool(block.get("insufficient", True)),
        "high_cases": [_case(i) for i in (block.get("high") or [])[:cases_limit]],
        "low_cases": [_case(i) for i in (block.get("low") or [])[:cases_limit]],
    }


# ---------------------------------------------------------------- 主入口

def build_rewrite_strategy_context(
    *,
    source_analysis: dict[str, Any] | None = None,
    writing_profile: dict[str, Any] | None = None,
    performance_profile: dict[str, Any] | None = None,
    cross_insights: dict[str, Any] | None = None,
    reference_author_id: int | None = None,
    goal: str | None = None,
) -> dict[str, Any]:
    """确定性组装 Rewrite Context。**缺什么跳什么，绝不阻塞生成**。

    单稿与三候选**共用**这一份组装逻辑（任务 04-A 修 D1/D4）：
    旧版只有三候选会调它，因此单稿永远拿不到历史表现洞察。
    """
    skipped: list[str] = []

    source = select_source_analysis(source_analysis)
    if source is None:
        skipped.append("source_analysis")

    writing = select_writing_context(writing_profile)
    if not writing:
        skipped.append("writing_profile")

    perf_high, perf_low = select_performance_context(
        performance_profile, source_analysis=source_analysis
    )
    if not perf_high and not perf_low:
        skipped.append("performance_profile")

    goal_context = select_goal_context(performance_profile, goal=goal)
    if goal_context is None:
        skipped.append("performance_goal")

    cross_high, cross_low = select_cross_context(cross_insights)
    if not cross_high and not cross_low:
        skipped.append("cross_insights")

    dependent = select_account_dependent_context(
        cross_insights, reference_author_id=reference_author_id
    )

    return {
        "source_analysis": source,
        "writing": writing,
        "performance_high": perf_high,
        "performance_low": perf_low,
        # D9：书写目标对应的表现指标（默认 exposure，行为与旧版一致）
        "performance_goal": goal_context,
        "cross_high": cross_high,
        "cross_low": cross_low,
        "account_dependent": dependent,
        # 缺失层如实记录，供 UI 显示 ✓ / —（不是 AI 生成的说明）
        "skipped": skipped,
        "reference_author_id": reference_author_id,
        "budget": {
            "max_writing_metrics": MAX_WRITING_METRICS,
            "max_performance_high": MAX_PERFORMANCE_HIGH,
            "max_performance_low": MAX_PERFORMANCE_LOW,
            "max_goal_cases": MAX_GOAL_CASES,
            "max_cross_high": MAX_CROSS_HIGH,
            "max_cross_low": MAX_CROSS_LOW,
            "max_account_dependent": MAX_ACCOUNT_DEPENDENT,
        },
    }


def context_is_empty(context: dict[str, Any]) -> bool:
    """没有任何一层可用 → 调用方仍然照常生成（只是没有数据上下文）。"""
    return not any(
        (
            context.get("source_analysis"),
            context.get("writing"),
            context.get("performance_high"),
            context.get("performance_low"),
            context.get("performance_goal"),
            context.get("cross_high"),
            context.get("cross_low"),
            context.get("account_dependent"),
        )
    )


def strategy_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    """把 context 拍平成结构化条目，供候选卡生成 strategy_tags。

    **由代码生成**，绝不是模型自报。每条都带 dimension，
    这样才能按候选角色（稳 / 猛 / 新）挑出真正相关的那几条。
    """
    out: list[dict[str, Any]] = []
    for item in context.get("cross_high") or []:
        out.append(
            {
                "source": "cross",
                "dimension": item.get("dimension"),
                "label": item.get("label"),
                "value": item.get("value"),
            }
        )
    for item in context.get("performance_high") or []:
        out.append(
            {
                "source": "perf",
                "dimension": item.get("dimension"),
                "label": item.get("label"),
                "value": item.get("value"),
            }
        )
    for item in context.get("account_dependent") or []:
        out.append(
            {
                "source": "dep",
                "dimension": item.get("dimension"),
                "label": item.get("label"),
                "value": item.get("value"),
                "stance": item.get("reference_stance"),
            }
        )
    for item in context.get("writing") or []:
        values = item.get("values") or []
        if not values:
            continue
        out.append(
            {
                "source": "writing",
                "dimension": item.get("field"),
                "label": item.get("label"),
                "value": values[0].get("value"),
            }
        )
    return out


def _format_tag(item: dict[str, Any]) -> str:
    """候选卡上的短标签：**只给取值本身**。

    UI 空间有限，长成「跨账号高表现·主题·分化高主题」会像 debug trace。
    完整 context 不进 UI —— 需要解释时看 profile 页面。
    """
    value = item.get("value") or ""
    if item["source"] == "dep":
        return f"{value}（{'可用' if item.get('stance') == 'favor' else '避免'}）"
    return str(value)


# 04-C：角色换成三种传播机制后，各角色优先看的维度也跟着换。
#   A 反差切入 → 开头机制、修辞动作、直接程度（"怎么开、怎么制造反差"）
#   B 结论前置 → 直接程度、确定性、节奏（"有多硬、有多快"）
#   C 场景重构 → 推进结构、修辞动作、结尾、语气（"换个什么结构讲"）
# 三者必须互不相同 —— 否则候选卡上的「参考」会三个一模一样，
# 看起来就像系统没真的按角色挑（Stage 10 验收项 33c 锁的正是这点）。
ROLE_PRIORITY_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "A": (
        "writing.opening_type",
        "writing.moves[].type",
        "style.directness",
    ),
    "B": (
        "style.directness",
        "style.certainty",
        "style.rhythm",
    ),
    "C": (
        "writing.structure_type",
        "writing.moves[].type",
        "writing.ending_type",
        "style.tone",
    ),
}


def tags_for_role(context: dict[str, Any], role: str, limit: int = 3) -> list[str]:
    """按候选角色挑 strategy_tags：A 反差切入 / B 结论前置 / C 场景重构。

    两道必要的过滤，否则候选卡会出现自相矛盾的「参考」：
      1. 写法画像里**出现在本作者低表现榜**的取值一律剔除
         （不能让「强钩子版」引用一个历史上表现很差的开头方式）；
      2. 按 (dimension, value) 去重
         （同一个取值可能同时来自跨账号共性与单账号高表现）。
    """
    items = strategy_items(context)
    low_keys = {
        (i.get("dimension"), i.get("value"))
        for i in context.get("performance_low") or []
    }
    items = [
        i
        for i in items
        if not (
            i["source"] == "writing"
            and (i.get("dimension"), i.get("value")) in low_keys
        )
    ]

    dims = ROLE_PRIORITY_DIMENSIONS.get(role, ())
    picked: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()

    def add(item: dict[str, Any]) -> None:
        key = (item.get("dimension"), item.get("value"))
        if key in seen:
            return
        seen.add(key)
        picked.append(item)

    if dims:
        for item in items:
            if item.get("dimension") in dims:
                add(item)
    for item in items:
        if len(picked) >= limit:
            break
        add(item)
    return [_format_tag(i) for i in picked[:limit]]
