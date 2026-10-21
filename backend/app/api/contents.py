"""内容相关接口：创建、列表、详情、删除、重跑分析、筛选项。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app import repository as repo
from app.config import settings
from app.db import get_conn
from app.jobs import enqueue_analysis, notify_style_evidence_changed
from app.schemas import (
    BulkDeleteIn,
    BulkEditIn,
    ContentCreate,
    ContentDetail,
    ContentListOut,
    FacetsOut,
    StyleEligibleIn,
)
from app.services.errors import NotFoundError
from app.services.retriever import similar_for_content

router = APIRouter(prefix="/api", tags=["contents"])


@router.post("/contents/bulk-delete")
def bulk_delete(payload: BulkDeleteIn) -> dict:
    """批量删除内容（物理删除，关联数据由外键级联清理）。"""
    if not payload.ids:
        return {"deleted": 0}
    with get_conn(settings.db_path) as conn:
        deleted = repo.delete_contents(conn, payload.ids)
    return {"deleted": deleted}


@router.post("/contents/bulk-edit")
def bulk_edit(payload: BulkEditIn) -> dict:
    """批量修改来源 / 作者 / 风格学习资格。

    三个字段各自独立：
    - 改 source **不会**改动 style_eligible（两个维度互不干涉）
    - 改 style_eligible 只影响 source='mine' 的内容
    - 全部不触碰 content_id / external_id / import_batch_id / analysis，也不调用 AI
    """
    if not payload.ids:
        return {"updated": 0, "source_updated": 0, "author_updated": 0,
                "style_eligible_updated": 0}
    if (
        payload.source is None
        and payload.author_id is None
        and payload.author_username is None
        and not payload.clear_author
        and payload.style_eligible is None
    ):
        raise HTTPException(status_code=400, detail="未指定要修改的字段")

    source_updated = 0
    author_updated = 0
    eligible_updated = 0
    with get_conn(settings.db_path) as conn:
        if payload.source is not None:
            source_updated = repo.update_contents_source(
                conn, payload.ids, payload.source
            )
        if payload.clear_author:
            # 用户显式请求清除作者：author_id 与 author_username 同时置 NULL
            author_updated = repo.set_content_author(conn, payload.ids, None)
        elif payload.author_id is not None:
            author_updated = repo.set_content_author(
                conn, payload.ids, payload.author_id
            )
        elif payload.author_username is not None:
            # 兼容旧调用：传 username 时 resolve 到正式 author（不存在则创建）
            author_id = (
                repo.resolve_author(conn, payload.author_username)
                if payload.author_username
                else None
            )
            author_updated = repo.set_content_author(
                conn, payload.ids, author_id
            )
        if payload.style_eligible is not None:
            eligible_updated = repo.update_style_eligible(
                conn, payload.ids, payload.style_eligible
            )

    # 风格证据集合发生变化 → 通知现有 STYLE 机制检查是否失效
    if eligible_updated or source_updated:
        notify_style_evidence_changed()

    return {
        "updated": max(source_updated, author_updated, eligible_updated),
        "source_updated": source_updated,
        "author_updated": author_updated,
        "style_eligible_updated": eligible_updated,
    }


@router.post("/contents/{content_id}/style-eligible")
def set_style_eligible(content_id: int, payload: StyleEligibleIn) -> dict:
    """单条设置「参与风格学习」。benchmark 内容永远无法开启。"""
    with get_conn(settings.db_path) as conn:
        row = repo.get_content_raw(conn, content_id)
        if row is None:
            raise NotFoundError(f"内容 {content_id} 不存在")
        if row["source"] != "mine":
            raise HTTPException(
                status_code=400,
                detail="对标内容不参与风格学习（开关只对「我的内容」有效）",
            )
        updated = repo.update_style_eligible(conn, [content_id], payload.eligible)

    if updated:
        notify_style_evidence_changed()
    return {
        "content_id": content_id,
        "style_eligible": bool(payload.eligible),
        "updated": updated,
    }


@router.post("/contents", response_model=ContentDetail)
def create_content(payload: ContentCreate) -> ContentDetail:
    raw_text = payload.raw_text.strip()
    if not raw_text:
        raise HTTPException(status_code=400, detail="内容不能为空")

    # 手动输入的「我的」内容 = human_original（最强的个人风格证据之一）
    provenance = payload.provenance
    if payload.source == "mine" and provenance is None:
        provenance = "human_original"
    elif payload.source != "mine":
        provenance = None

    with get_conn(settings.db_path) as conn:
        # 是否参与风格学习：
        # - 显式传值 → 尊重调用方（例如「我的灵感」是本人所写但不成文，必须 False）
        # - 未指定   → 沿用既有默认规则（mine 可学、benchmark 不可学）
        # benchmark 即便被误传 True，create_content 内部也会强制归 0（双保险）。
        eligible = payload.style_eligible
        if eligible is None:
            eligible = payload.source == "mine"

        content_id = repo.create_content(
            conn,
            payload.source,
            raw_text,
            style_eligible=eligible,
        )
        if provenance:
            repo.set_provenance(conn, content_id, provenance)
        # 对标作者：始终经由正式 author 写入，author_id 与 author_username 一起维护
        if payload.author_id is not None:
            repo.set_content_author(conn, [content_id], payload.author_id)
        # 原链接：复用已有的 source_url 列（不新增字段）
        if payload.source_url:
            repo.set_content_source_meta(
                conn, content_id, {"source_url": payload.source_url.strip()}
            )
        row = repo.get_content(conn, content_id)
        detail = _to_detail(conn, row)

    if payload.analyze:
        # 用户主动输入 = 已授权分析：先记为 queued，再入队。
        # 这样即使此刻没有密钥（入队失败），配置密钥重启后仍会被恢复分析。
        with get_conn(settings.db_path) as conn:
            repo.set_analysis_state(conn, content_id, "queued")
        enqueue_analysis(content_id)

    return ContentDetail(**detail)


@router.get("/contents", response_model=ContentListOut)
def list_contents(
    source: Literal["mine", "benchmark"] | None = None,
    q: str | None = None,
    tags: list[str] | None = Query(default=None),
    topic: str | None = None,
    content_type: str | None = None,
    status: str | None = None,
    author_username: str | None = None,
    author_id: int | None = None,
    import_batch_id: str | None = None,
    style_eligible: bool | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ContentListOut:
    with get_conn(settings.db_path) as conn:
        items, total = repo.list_contents(
            conn,
            source=source,
            q=q,
            tags=tags,
            topic=topic,
            content_type=content_type,
            status=status,
            author_username=author_username,
            author_id=author_id,
            import_batch_id=import_batch_id,
            style_eligible=style_eligible,
            limit=limit,
            offset=offset,
        )
    return ContentListOut(items=items, total=total, limit=limit, offset=offset)


@router.get("/contents/{content_id}", response_model=ContentDetail)
def get_content(content_id: int) -> ContentDetail:
    with get_conn(settings.db_path) as conn:
        row = repo.get_content(conn, content_id)
        if row is None:
            raise NotFoundError(f"内容 {content_id} 不存在")
        return ContentDetail(**_to_detail(conn, row))


@router.delete("/contents/{content_id}")
def delete_content(content_id: int) -> dict:
    """删除内容（物理删除）。"""
    with get_conn(settings.db_path) as conn:
        row = repo.get_content_raw(conn, content_id)
        if row is None:
            raise NotFoundError(f"内容 {content_id} 不存在")
        conn.execute("DELETE FROM contents WHERE id = ?", (content_id,))
        conn.execute(
            "UPDATE tags SET count = (SELECT COUNT(*) FROM content_tags ct WHERE ct.tag_id = tags.id)"
        )
        conn.execute("DELETE FROM tags WHERE count = 0 AND kind = 'tag'")
    return {"deleted": content_id}


@router.post("/contents/{content_id}/analyze", response_model=ContentDetail)
def reanalyze(content_id: int) -> ContentDetail:
    with get_conn(settings.db_path) as conn:
        row = repo.get_content_raw(conn, content_id)
        if row is None:
            raise NotFoundError(f"内容 {content_id} 不存在")
        # 用户已显式点击分析 → 状态必须是 queued（一次写完两列）。
        # 这与另一条入队路径（导入时 analyze=true）以及 contents 表里
        # 「queued = 用户已点击分析，等待 worker」的定义一致。
        # 不这样写的话，worker 的 pre-flight 守卫会把这次请求当成
        # 「已交接出去 / 不该分析」而跳过（Stage 12.1）。
        repo.set_analysis_status(conn, content_id, "queued")
    enqueue_analysis(content_id)
    with get_conn(settings.db_path) as conn:
        row = repo.get_content(conn, content_id)
        return ContentDetail(**_to_detail(conn, row))


@router.get("/facets", response_model=FacetsOut)
def facets(
    source: Literal["mine", "benchmark"] | None = None,
    q: str | None = None,
    tags: list[str] | None = Query(default=None),
    topic: str | None = None,
    content_type: str | None = None,
    status: str | None = None,
    author_username: str | None = None,
    author_id: int | None = None,
    import_batch_id: str | None = None,
    style_eligible: bool | None = None,
) -> FacetsOut:
    """左侧筛选项统计。

    参数与 /contents 完全一致：facets 必须反映**当前筛选结果**，
    否则会出现「右侧 0 条、左侧一堆筛选项」的不一致。
    """
    with get_conn(settings.db_path) as conn:
        data = repo.list_facets(
            conn,
            source=source,
            q=q,
            tags=tags,
            topic=topic,
            content_type=content_type,
            status=status,
            author_username=author_username,
            author_id=author_id,
            import_batch_id=import_batch_id,
            style_eligible=style_eligible,
        )
    return FacetsOut(**data)


def _to_detail(conn, row) -> dict:
    """把数据库行转换为 ContentDetail 所需的字典（含分析、相似内容）。"""
    base = repo._row_to_content(row)
    content_id = base["id"]

    analysis = repo.get_analysis(conn, content_id)
    raw = repo.get_content_raw(conn, content_id)

    similar = []
    if analysis:
        similar = similar_for_content(conn, content_id, k=5)

    base["raw_text"] = raw["raw_text"] if raw else ""
    base["analysis"] = analysis
    base["performance"] = repo.get_content_performance(conn, content_id)
    # 来源元数据：语言 / 是否回复 / 话题标签 / 链接 / 媒体地址
    # （任务 04-A 起从 Xporter 保留下来，供查看与后续研究）
    base["source_meta"] = repo.get_content_source_meta(conn, content_id)
    base["similar"] = [
        {
            "id": s["id"],
            "source": s["source"],
            "excerpt": s["excerpt"],
            "topic": s.get("topic"),
            "content_type": s.get("content_type"),
            "score": s["score"],
            "shared_tags": s.get("shared_tags", []),
        }
        for s in similar
    ]
    return base
