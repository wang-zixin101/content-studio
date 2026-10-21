"""分析结果落库的**唯一共同原语**（Stage 12 新增）。

------------------------------------------------------------------
背景
------------------------------------------------------------------
Stage 12 开发前核查发现：realtime（analyzer.analyze_content）与
Batch（batch._save_analysis）各自写了一遍完全相同的落库副作用。
外部分析导入如果照抄第三遍，就会出现三份会各自漂移的实现。

因此抽成本函数，三条路径**全部**调用它：

    Realtime    → persist_analysis_result
    Batch       → persist_analysis_result
    External    → persist_analysis_result

------------------------------------------------------------------
职责边界（严格）
------------------------------------------------------------------
**调用方必须先通过 analysis_spec.validate_analysis()** ——
本函数不做合同校验，它的职责只是「把已经合法的结果写下去」。
缺字段的半成品绝不允许经过这里进入 analyses 表。

只包含这三条路径**原本就共有**的持久化副作用：

    1. repo.save_analysis（UPSERT analyses 单行，含扁平索引列 + 完整 JSON）
    2. repo.replace_content_tags（tag / topic / content_type 关联）
    3. repo.set_summary（摘要回填）
    4. repo.set_analysis_status(analyzed)（唯一的双列状态入口）

**不包含**（刻意排除，避免把调度混进持久化）：

    - provider 调用 / 重试 / 退避
    - 队列入队 / 进度上报 / run 计数
    - 风格档案通知（由调用方在合适时机触发一次）
    - 事务边界（由调用方决定：外部分析整批一个 transaction）
    - length_chars（本地计算量，由调用方补写到索引列）
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app import repository as repo


def tag_items_for(normalized: dict[str, Any]) -> list[tuple[str, str]]:
    """从 normalized 结果推导 content_tags 关联项。

    与 realtime / Batch 原来的写法逐字一致：
    tags → kind='tag'，topic → kind='topic'，content_type → kind='content_type'。
    """
    items: list[tuple[str, str]] = [(t, "tag") for t in normalized["tags"]]
    if normalized["topic"]:
        items.append((normalized["topic"], "topic"))
    if normalized["content_type"]:
        items.append((normalized["content_type"], "content_type"))
    return items


def summary_for(normalized: dict[str, Any], raw_text: str) -> str:
    """摘要回填规则：核心含义 → 主题 → 正文前 80 字。

    注意用 `core_message`（新合同的字段）而不是旧的 `core_viewpoint`：
    新合同允许没有明确论点的内容只概括其实际表达，摘要照样有值。
    """
    return normalized.get("core_message") or normalized.get("topic") or (raw_text or "")[:80]


def persist_analysis_result(
    conn: sqlite3.Connection,
    content_id: int,
    normalized: dict[str, Any],
    raw: dict[str, Any],
    model: str,
    raw_text: str,
) -> None:
    """把一份分析结果落库。

    **调用方必须先通过 analysis_spec.validate_analysis()** —— 本函数不做校验，
    它的职责只是「把已经合法的结果写下去」，三条分析入口共用同一套副作用。

    参数 raw 是要存进 analyses.analysis_json 的**完整规范结果**；
    对内部路径是校验通过的模型输出，对外部分析是结果文件里的 analysis 对象。
    两者现在是同一种东西：都必须是合同对象。
    """
    repo.save_analysis(conn, content_id, raw, model, raw=raw)
    repo.replace_content_tags(conn, content_id, tag_items_for(raw))
    repo.set_summary(conn, content_id, summary_for(raw, raw_text))
    repo.set_analysis_status(conn, content_id, "analyzed")
