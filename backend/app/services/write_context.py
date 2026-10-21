"""写作资料组装器（任务 04-A 新增）。

------------------------------------------------------------------
为什么需要它
------------------------------------------------------------------
旧版两个写作出口各取各的资料：
    - 单稿（rewriter.generate_draft）：只拿 get_analysis 的扁平列，
      没有内容不变量、没有历史数据规律 → D1 + 「单稿零历史策略」；
    - 三候选（rewrite_candidates.generate_candidates）：单独取 invariants，
      不接收 reference_profile → D4。

结果就是「同一份素材，两个出口拿到的资料不一样」。本模块把取数逻辑
收敛成**唯一一份**，单稿与三候选都调用它。

------------------------------------------------------------------
纪律
------------------------------------------------------------------
- **0 AI**：只做确定性读取与组装，不触发任何模型调用、不自动补分析；
- **缺什么跳什么**：任一层没有数据只是被记进 skipped[]，绝不报错阻塞；
- **不编造**：拿不到的数据就是拿不到，不生成占位值。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app import repository as repo
from app.services import cross_account
from app.services import performance_profile as perf
from app.services.errors import NotFoundError
from app.services.rewrite_strategy import build_rewrite_strategy_context


def safe_writing_profile(
    conn: sqlite3.Connection, author_id: int
) -> dict[str, Any] | None:
    """读取写作画像；**没有就返回 None，绝不报错、绝不自动分析**。

    作者不存在时仍然报 404 —— 那是调用方给了错误 ID，不是「没有数据」。
    """
    author = repo.get_author(conn, author_id)
    if author is None:
        raise NotFoundError(f"参考作者 {author_id} 不存在")
    profile = repo.author_writing_profile(conn, author_id)
    if int(profile.get("analyzed_sample_count") or 0) <= 0:
        return None
    return profile


def safe_performance_profile(
    conn: sqlite3.Connection, author_id: int
) -> dict[str, Any] | None:
    """读取流量画像；没有任何有效样本 → None（跳过，不阻塞）。

    判定门槛是 **eligible_count**（有效曝光 + 非置顶 + ≥48h），
    不再要求 analyzed_eligible_count > 0 ——
    次级互动指标（点赞率 / 评论率 …）只需要 eligible 样本，
    要求必须先分析完才能看互动数据是没有依据的限制（D9）。
    维度类数据在样本未分析时自然为空，由消费方按空处理。
    """
    data = perf.author_performance_profile(conn, author_id)
    summary = data.get("sample_summary") or {}
    if int(summary.get("eligible_count") or 0) <= 0:
        return None
    return data


def build_write_context(
    conn: sqlite3.Connection,
    *,
    source_analysis: dict[str, Any] | None,
    reference_author_id: int | None = None,
    goal: str | None = None,
    cross_insights: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """组装两个写作出口共用的策略上下文。

    cross_insights 允许调用方传入已算好的一份（三候选会复用），
    避免同一次生成里把跨账号聚合算两遍。
    """
    writing_profile = (
        safe_writing_profile(conn, reference_author_id)
        if reference_author_id is not None
        else None
    )
    performance_profile = (
        safe_performance_profile(conn, reference_author_id)
        if reference_author_id is not None
        else None
    )
    if cross_insights is None:
        cross_insights = cross_account.cross_account_insights(conn)

    return build_rewrite_strategy_context(
        source_analysis=source_analysis,
        writing_profile=writing_profile,
        performance_profile=performance_profile,
        cross_insights=cross_insights,
        reference_author_id=reference_author_id,
        goal=goal,
    )
