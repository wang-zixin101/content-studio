"""素材池接口（阶段 10）：随机素材入口的数据来源。

全部 0 AI、只读。

素材池口径：
    source = 'benchmark' 且 raw_text 非空

「空白作者」不是一个 Author，只是 author_id IS NULL 的一个池，
库里绝不存在「空白作者 / Unknown / 未分类」这类假作者记录。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query

from app import repository as repo
from app.config import settings
from app.db import get_conn
from app.schemas import MaterialOut, MaterialPoolsOut
from app.services.errors import NotFoundError

router = APIRouter(prefix="/api/materials", tags=["materials"])

MAX_EXCLUDE = 500


@router.get("/pools", response_model=MaterialPoolsOut)
def material_pools() -> MaterialPoolsOut:
    """素材池计数：全部 / 空白作者 / 每个正式作者的 benchmark 素材数。"""
    with get_conn(settings.db_path) as conn:
        data = repo.count_material_pools(conn)
    return MaterialPoolsOut(**data)


@router.get("/random", response_model=MaterialOut)
def random_material(
    pool: Literal["all", "blank", "author"] = "all",
    author_id: int | None = None,
    exclude_ids: list[int] = Query(default=[]),
    unused_first: bool = True,
) -> MaterialOut:
    """随机抽一条素材。

    - pool=blank  → author_id IS NULL（不建假作者）
    - pool=author → 必须传 author_id
    - exclude_ids → 当前 session 已展示过的素材，前端负责维护（不建 seen 表）
    - unused_first → 优先抽「从未被改写过」的；用尽自动降级（返回 fallback_used=True）

    素材本身**随机**：不做高曝光加权、不做 Top10% 过滤（那会迅速同质化）。
    """
    if pool == "author" and author_id is None:
        raise NotFoundError("pool=author 必须提供 author_id")

    exclude = [int(i) for i in exclude_ids if i][:MAX_EXCLUDE]

    with get_conn(settings.db_path) as conn:
        material, fallback = repo.random_material(
            conn,
            pool=pool,
            author_id=author_id,
            exclude_ids=exclude,
            unused_first=unused_first,
        )

    if material is None:
        raise NotFoundError("该素材池里没有可用素材")

    return MaterialOut(
        content_id=int(material["content_id"]),
        raw_text=material["raw_text"] or "",
        author_id=material["author_id"],
        author_username=material["author_username"],
        author_display_name=material["author_display_name"],
        posted_at=material["posted_at"],
        view_count=material["view_count"],
        media_type=material["media_type"],
        fallback_used=fallback,
    )
