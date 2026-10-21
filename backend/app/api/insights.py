"""跨账号流量画像 API（阶段 9）。

0 AI / 0 token：只读 Stage 8 已算好的单账号画像，在内存里做作者等权聚合。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app import repository as repo
from app.config import settings
from app.db import get_conn
from app.schemas import CrossAccountInsights
from app.services import cross_account as svc

router = APIRouter(prefix="/api/performance-insights", tags=["performance-insights"])


@router.get("/cross-account", response_model=CrossAccountInsights)
def cross_account_insights(
    author_ids: str | None = Query(
        None, description="可选：参与比较的正式 author_id，逗号分隔，如 1,2,3"
    ),
) -> CrossAccountInsights:
    """跨账号流量画像 V1：**作者等权**的确定性聚合，0 AI，现算不缓存。

    比较基准是各作者**内部**已标准化的 Performance Score，
    绝不直接比较绝对曝光；一个作者 = 一票，帖子数不做权重。
    """
    parsed: list[int] | None = None
    if author_ids:
        try:
            parsed = [int(p) for p in author_ids.split(",") if p.strip()]
        except ValueError as err:
            raise HTTPException(
                status_code=400, detail="author_ids 必须是逗号分隔的整数"
            ) from err
        if not parsed:
            raise HTTPException(status_code=400, detail="author_ids 不能为空")

    with get_conn(settings.db_path) as conn:
        if parsed:
            for aid in parsed:
                if repo.get_author(conn, aid) is None:
                    raise HTTPException(
                        status_code=404, detail=f"作者 {aid} 不存在"
                    )
        try:
            data = svc.cross_account_insights(conn, parsed)
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
    return CrossAccountInsights(**data)
