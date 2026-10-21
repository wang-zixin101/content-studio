"""风格档案接口：读取最新档案、重算、新鲜度状态。"""

from __future__ import annotations

from fastapi import APIRouter

from app import repository as repo
from app.config import settings
from app.db import get_conn
from app.schemas import StyleProfileOut
from app.services.style_profile import profile_staleness, recompute_style_profile

router = APIRouter(prefix="/api/style", tags=["style"])


@router.get("/profile", response_model=StyleProfileOut | None)
def get_profile() -> StyleProfileOut | None:
    with get_conn(settings.db_path) as conn:
        profile = repo.latest_style_profile(conn)
    return StyleProfileOut(**profile) if profile else None


@router.post("/recompute", response_model=StyleProfileOut)
async def recompute() -> StyleProfileOut:
    with get_conn(settings.db_path) as conn:
        profile = await recompute_style_profile(conn)
    return StyleProfileOut(**profile)


@router.get("/status")
def status() -> dict:
    with get_conn(settings.db_path) as conn:
        return profile_staleness(conn)


@router.get("/evidence")
def evidence() -> dict:
    """风格证据审计：当前有效样本构成，以及被排除的数量。

    有效样本 = source='mine' AND style_eligible=1 AND status='analyzed'
    """
    with get_conn(settings.db_path) as conn:
        stats = repo.mine_sample_stats(conn)
        profile = repo.latest_style_profile(conn)
        invalidated = (
            repo.count_invalidated_samples(conn, profile["sample_ids"])
            if profile
            else 0
        )
        excluded_row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM contents c
              JOIN analyses a ON a.content_id = c.id
             WHERE c.source = 'mine' AND c.status = 'analyzed'
               AND COALESCE(c.style_eligible, 0) = 0
            """
        ).fetchone()
        eligible_ids = repo.list_style_eligible_ids(conn)

    return {
        "eligible_count": stats["sample_count"],
        "human_original": stats["human_original_count"],
        "imported_self": stats["imported_self_count"],
        "ai_assisted": stats["ai_assisted_count"],
        # 「我的内容」中因关闭资格而未参与的数量
        "excluded_count": int(excluded_row["n"]),
        "profile_version": profile["version"] if profile else None,
        # 当前档案实际基于多少条
        "profile_sample_count": len(profile["sample_ids"]) if profile else 0,
        # 当前档案中已失效的样本数（>0 表示档案需要重算）
        "profile_invalidated_samples": invalidated,
        "sample_ids": eligible_ids,
    }
