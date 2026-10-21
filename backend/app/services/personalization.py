"""Personalization 状态判定。

依据「有效风格样本」的数量与多样性，判定：

    COLD_START    0 ~ 4 个有效样本  —— 没有可靠个人风格证据
    LEARNING      5 ~ 9            —— 证据不足，参考但标低置信度
    PERSONALIZED  >= 10 且多样性达标 —— 可使用完整风格证据链

有效样本 = 已成功分析、且属于「我的内容」的 final text。
AI 初稿只存在于 rewrites 表，永远不写入 contents，因此天然不参与统计。

阈值来自 config，属**产品默认值**，不是科学结论。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app import repository as repo
from app.config import (
    COLD_START_MAX,
    LEARNING_MAX,
    MIN_CONTENT_TYPES_FOR_PERSONALIZED,
    MIN_TOPICS_FOR_PERSONALIZED,
    PERSONALIZED_MIN,
)

COLD_START = "COLD_START"
LEARNING = "LEARNING"
PERSONALIZED = "PERSONALIZED"

ALL_STATES = (COLD_START, LEARNING, PERSONALIZED)


def resolve_state(conn: sqlite3.Connection) -> dict[str, Any]:
    """返回当前 personalization 状态及其依据。"""
    stats = repo.mine_sample_stats(conn)
    count = stats["sample_count"]

    if count <= COLD_START_MAX:
        state, confidence = COLD_START, "low"
        reason = f"仅有 {count} 个确认样本（≤{COLD_START_MAX}），没有可靠的个人风格证据"
    elif count <= LEARNING_MAX:
        state, confidence = LEARNING, "low"
        reason = f"有 {count} 个确认样本（{COLD_START_MAX + 1}~{LEARNING_MAX}），证据尚不充分"
    else:
        diverse = (
            stats["topic_count"] >= MIN_TOPICS_FOR_PERSONALIZED
            and stats["content_type_count"] >= MIN_CONTENT_TYPES_FOR_PERSONALIZED
        )
        if diverse:
            state, confidence = PERSONALIZED, "medium"
            reason = (
                f"有 {count} 个确认样本，且覆盖 {stats['topic_count']} 个主题 / "
                f"{stats['content_type_count']} 个内容类型，证据较充分"
            )
        else:
            state, confidence = LEARNING, "low"
            reason = (
                f"样本数 {count} 已达标，但多样性不足（{stats['topic_count']} 个主题 / "
                f"{stats['content_type_count']} 个内容类型），暂不进入 PERSONALIZED"
            )

    return {
        "state": state,
        "confidence": confidence,
        "reason": reason,
        **stats,
        "thresholds": {
            "cold_start_max": COLD_START_MAX,
            "learning_max": LEARNING_MAX,
            "personalized_min": PERSONALIZED_MIN,
            "min_topics": MIN_TOPICS_FOR_PERSONALIZED,
            "min_content_types": MIN_CONTENT_TYPES_FOR_PERSONALIZED,
        },
    }


def current_state(conn: sqlite3.Connection) -> str:
    return resolve_state(conn)["state"]


def has_reliable_voice(conn: sqlite3.Connection) -> bool:
    """是否具备可用的个人风格证据（LEARNING 及以上）。"""
    return current_state(conn) in (LEARNING, PERSONALIZED)
