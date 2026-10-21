"""作者管理：正式作者实体及其最小管理能力。

设计要点：

- 作者身份 = platform + normalized username（X 用户名大小写不敏感）
- 作者的帖子数 / 已分析数**实时聚合**，不做同步字段
- 删除作者时若仍有关联内容则拒绝，**绝不 CASCADE 删除 contents**
- 作者与 mine / benchmark 完全独立：不能根据作者推断来源
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app import repository as repo
from app.config import settings
from app.db import get_conn
from app.schemas import (
    AuthorCreate,
    AuthorOverview,
    AuthorPerformanceProfile,
    AuthorUpdate,
    AuthorWritingProfile,
    PerformanceEvidenceItem,
    ProfileEvidenceItem,
)
from app.services import performance_profile as perf

logger = logging.getLogger("content_studio.authors")

router = APIRouter(prefix="/api/authors", tags=["authors"])


@router.get("")
def list_authors(q: str | None = None, with_content: bool = False) -> dict:
    """作者列表（含 content_count / analyzed_count），支持搜索。

    with_content=true —— 只返回至少有一条内容的作者（内容库「作者模式」口径）。
    默认 false —— 返回全部正式作者（作者管理页口径，含 0 内容作者）。
    """
    with get_conn(settings.db_path) as conn:
        items = repo.list_authors(conn, q=q, with_content=with_content)
    return {"items": items, "total": len(items)}


@router.post("")
def create_author(payload: AuthorCreate) -> dict:
    """新增作者。**即使还没有任何帖子也可以创建。**"""
    with get_conn(settings.db_path) as conn:
        try:
            author_id = repo.create_author(
                conn,
                username=payload.username,
                display_name=payload.display_name,
                notes=payload.notes,
            )
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        except Exception as err:  # noqa: BLE001 —— 唯一约束冲突
            if "UNIQUE" in str(err).upper():
                raise HTTPException(
                    status_code=409,
                    detail=f"作者已存在：@{repo.normalize_author_username(payload.username)}",
                ) from err
            raise
        return repo.get_author(conn, author_id) or {}


@router.get("/{author_id}")
def get_author(author_id: int) -> dict:
    with get_conn(settings.db_path) as conn:
        author = repo.get_author(conn, author_id)
    if author is None:
        raise HTTPException(status_code=404, detail=f"作者 {author_id} 不存在")
    return author


@router.get("/{author_id}/overview", response_model=AuthorOverview)
def author_overview(author_id: int) -> AuthorOverview:
    """作者概览。**纯聚合，不调用任何 AI，不会触发分析。**"""
    with get_conn(settings.db_path) as conn:
        if repo.get_author(conn, author_id) is None:
            raise HTTPException(status_code=404, detail=f"作者 {author_id} 不存在")
        return AuthorOverview(**repo.author_overview(conn, author_id))


@router.get("/{author_id}/writing-profile", response_model=AuthorWritingProfile)
def author_writing_profile(author_id: int) -> AuthorWritingProfile:
    """作者写作画像：对现有 ANALYSIS V2 结果做聚合。

    **纯查询，不调用任何 AI，不会触发分析、不会补分析。**
    样本 = 该作者已分析且确有分析结果的帖子；样本不足时也只如实显示覆盖率。
    """
    with get_conn(settings.db_path) as conn:
        if repo.get_author(conn, author_id) is None:
            raise HTTPException(status_code=404, detail=f"作者 {author_id} 不存在")
        return AuthorWritingProfile(**repo.author_writing_profile(conn, author_id))


@router.get(
    "/{author_id}/writing-profile/evidence",
    response_model=list[ProfileEvidenceItem],
)
def author_writing_profile_evidence(
    author_id: int,
    field: str,
    value: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[ProfileEvidenceItem]:
    """某个画像取值的全部证据帖（确定性匹配，不含完整正文）。"""
    with get_conn(settings.db_path) as conn:
        if repo.get_author(conn, author_id) is None:
            raise HTTPException(status_code=404, detail=f"作者 {author_id} 不存在")
        try:
            items, _total = repo.author_profile_evidence(
                conn, author_id, field, value, limit=limit, offset=offset
            )
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
    return [ProfileEvidenceItem(**item) for item in items]


@router.get(
    "/{author_id}/performance-profile", response_model=AuthorPerformanceProfile
)
def author_performance_profile(author_id: int) -> AuthorPerformanceProfile:
    """单账号流量画像：**确定性历史统计，0 AI，现算不缓存**。

    两个样本池严格分开：
      - eligible（有效曝光 + 非置顶 + 已发布 ≥48h）→ 账号基线 / 百分位
      - analyzed eligible（再要求有 ANALYSIS V2）→ 主题 / 写法 / 组合

    只回答「历史上表现如何」，不做爆款预测、不做因果推断、不跨账号。
    """
    with get_conn(settings.db_path) as conn:
        if repo.get_author(conn, author_id) is None:
            raise HTTPException(status_code=404, detail=f"作者 {author_id} 不存在")
        data = perf.author_performance_profile(conn, author_id)
    return AuthorPerformanceProfile(**data)


@router.get(
    "/{author_id}/performance-profile/evidence",
    response_model=list[PerformanceEvidenceItem],
)
def author_performance_evidence(
    author_id: int,
    value: str | None = None,
    dimension: str | None = None,
    combo: str | None = None,
    left_value: str | None = None,
    right_value: str | None = None,
    order: str = Query("high", pattern="^(high|low)$"),
    limit: int = Query(5, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[PerformanceEvidenceItem]:
    """某个取值 / 组合的真实证据帖（精确匹配）。

    dimension：topic / subtopic / content_type / 任一写作字段路径（配 value）
    combo    ：topic_opening / topic_structure / content_type_opening / topic_tone
               必须配 left_value + right_value，**结构化传参，
               绝不解析「A × B」展示字符串**——取值本身可能含 ×
    order    ：high = 表现分倒序（默认，先看最好的）
               low  = 表现分升序（低表现榜用，先看真正最差的）
    """
    with get_conn(settings.db_path) as conn:
        if repo.get_author(conn, author_id) is None:
            raise HTTPException(status_code=404, detail=f"作者 {author_id} 不存在")
        try:
            items, _total = perf.performance_evidence(
                conn,
                author_id,
                dimension=dimension,
                combo=combo,
                value=value,
                left_value=left_value,
                right_value=right_value,
                order=order,
                limit=limit,
                offset=offset,
            )
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
    return [PerformanceEvidenceItem(**item) for item in items]


@router.patch("/{author_id}")
def update_author(author_id: int, payload: AuthorUpdate) -> dict:
    """编辑作者。改 username 时会校验唯一冲突，并同步内容的兼容字段。"""
    with get_conn(settings.db_path) as conn:
        if repo.get_author(conn, author_id) is None:
            raise HTTPException(status_code=404, detail=f"作者 {author_id} 不存在")
        try:
            return repo.update_author(
                conn,
                author_id,
                username=payload.username,
                display_name=payload.display_name,
                notes=payload.notes,
            )
        except ValueError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err
        except Exception as err:  # noqa: BLE001
            if "UNIQUE" in str(err).upper():
                raise HTTPException(
                    status_code=409,
                    detail=f"用户名已被占用：@{repo.normalize_author_username(payload.username or '')}",
                ) from err
            raise


@router.delete("/{author_id}")
def delete_author(author_id: int) -> dict:
    """删除作者。仍有关联内容时**拒绝**（不会连删帖子）。"""
    with get_conn(settings.db_path) as conn:
        if repo.get_author(conn, author_id) is None:
            raise HTTPException(status_code=404, detail=f"作者 {author_id} 不存在")
        try:
            deleted = repo.delete_author(conn, author_id)
        except ValueError as err:
            raise HTTPException(status_code=409, detail=str(err)) from err
    logger.info("已删除作者 %s", author_id)
    return {"deleted": deleted, "id": author_id}
