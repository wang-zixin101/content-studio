"""写作接口：素材 / 想法 / 对标 → AI 初稿 → 用户编辑 → 保存为「我的内容」。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app import repository as repo
from app.config import settings
from app.db import get_conn
from app.schemas import (
    AdoptCandidateIn,
    AdoptCandidateOut,
    RewriteCandidateIn,
    RewriteCandidatesOut,
    RewriteIn,
    RewriteOut,
    RewriteSaveIn,
)
from app.config import WRITE_PROMPT_VERSION, settings
from app.services import rewriter
from app.services import rewrite_candidates as candidates_service
from app.services.errors import NotFoundError

router = APIRouter(prefix="/api", tags=["writes"])


# ---------------------------------------------------------------- 阶段 10：三候选
#
# 走「新增薄接口」而不是给 POST /api/rewrites 加可选模式：
# 旧的单稿契约一行都不动，回归风险最小；内部完全复用 rewriter / WRITE V2 / provider。


@router.post("/rewrite-candidates", response_model=RewriteCandidatesOut)
async def create_candidates(payload: RewriteCandidateIn) -> RewriteCandidatesOut:
    """一次生成 3 个候选（A 反差切入 / B 结论前置 / C 场景重构）。

    **恰好 1 次 WRITE AI**，解析失败明确报错，**绝不偷偷重试第二次**。
    本接口**无状态**：不写任何表——候选只是本轮临时结果，用户还没选择。

    任务 04-C：author_auto 路径下 source_text 与 source_content_id 都可以为空，
    由 AI 依据参考作者画像自主选题。是否合法由服务层统一判定。
    """
    source_id = payload.source_content_id
    source_text = (payload.source_text or "").strip()

    if not source_text and source_id is not None:
        with get_conn(settings.db_path) as conn:
            row = repo.get_content_raw(conn, source_id)
        if row is None:
            raise NotFoundError(f"内容 {source_id} 不存在")
        source_text = row["raw_text"]

    result = await candidates_service.generate_candidates(
        source_text=source_text,
        source_content_id=source_id,
        instruction=payload.instruction,
        reference_author_id=payload.reference_author_id,
        use_reference_profile=payload.use_reference_profile,
        goal=payload.goal,
        k=payload.examples_k,
        creation_path=payload.creation_path,
    )
    return RewriteCandidatesOut(**result)


@router.post("/rewrites/adopt", response_model=AdoptCandidateOut)
def adopt_candidate(payload: AdoptCandidateIn) -> AdoptCandidateOut:
    """采用某个候选 → 建一条正式 rewrite 记录（0 AI）。

    之后「编辑 → 保存为我的」完全走既有的 PUT /rewrites/{id} 链路，
    初稿→终稿的编辑距离照常计算，素材关联（benchmark_content_id）也保留下来。
    """
    text = (payload.text or "").strip()
    if not text:
        raise NotFoundError("候选正文为空")

    with get_conn(settings.db_path) as conn:
        rewrite_id = repo.create_rewrite(
            conn,
            benchmark_content_id=payload.source_content_id,
            ai_draft=text,
            instruction=payload.instruction,
            examples_used=[],
            style_profile_version=None,
            model=payload.model or settings.model_rewrite,
            prompt_version=WRITE_PROMPT_VERSION,
            personalization_state=None,
        )
        row = repo.get_rewrite(conn, rewrite_id)

    return AdoptCandidateOut(
        rewrite_id=int(row["id"]),
        ai_draft=row["ai_draft"],
        benchmark_content_id=row["benchmark_content_id"],
    )


@router.post("/rewrites", response_model=RewriteOut)
async def create_draft(payload: RewriteIn) -> RewriteOut:
    """生成一版初稿。

    素材可以直接粘贴（source_text），也可以引用一条已有内容
    （对标内容用 source_content_id）。COLD_START 下不会注入任何风格画像。

    新粘贴或尚未分析的内容照常生成 —— 没有 ANALYSIS 只是少一层参考，
    不阻塞、不自动补分析。

    任务 04-C 五种创作输入路径：
      rewrite / rewrite_author / topic / topic_author / author_auto。
    author_auto 下素材可以为空，由 AI 依据参考作者画像自主选题。

    参考作者画像：只有 use_reference_profile=True 时才由服务端按
    reference_author_id 读取正式画像并注入；默认 False = 仅记录来源。
    """
    source_id = payload.source_content_id or payload.benchmark_content_id
    source_text = (payload.source_text or "").strip()

    if not source_text and source_id is not None:
        with get_conn(settings.db_path) as conn:
            row = repo.get_content_raw(conn, source_id)
        if row is None:
            raise NotFoundError(f"内容 {source_id} 不存在")
        source_text = row["raw_text"]

    result = await rewriter.generate_draft(
        source_text=source_text,
        source_content_id=source_id,
        instruction=payload.instruction,
        base_text=payload.base_text,
        k=payload.examples_k,
        reference_author_id=payload.reference_author_id,
        use_reference_profile=payload.use_reference_profile,
        goal=payload.goal,
        creation_path=payload.creation_path,
    )
    return RewriteOut(**result)


@router.get("/rewrites/{rewrite_id}", response_model=RewriteOut)
def get_rewrite(rewrite_id: int) -> RewriteOut:
    return RewriteOut(**rewriter.get_detail(rewrite_id))


@router.put("/rewrites/{rewrite_id}", response_model=RewriteOut)
def save_rewrite(rewrite_id: int, payload: RewriteSaveIn) -> RewriteOut:
    """保存用户终稿。

    save_as_mine 为真时，终稿写入 contents(source='mine', provenance='ai_assisted')
    并自动进入分析队列；AI 初稿本身永远不会成为「我的内容」。
    """
    return RewriteOut(
        **rewriter.save_final(
            rewrite_id, payload.final_text, save_as_mine=payload.save_as_mine
        )
    )


@router.get("/contents/{content_id}/rewrites")
def list_rewrites(content_id: int, limit: int = Query(default=20, ge=1, le=100)) -> dict:
    with get_conn(settings.db_path) as conn:
        rows = repo.list_rewrites_for_content(conn, content_id)
    items = [
        {
            "id": r["id"],
            "ai_draft": r["ai_draft"],
            "final_text": r["final_text"],
            "is_saved_as_mine": bool(r["is_saved_as_mine"]),
            "saved_content_id": r["saved_content_id"],
            "edit_distance": r["edit_distance"],
            "edit_ratio": r["edit_ratio"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows[:limit]
    ]
    return {"items": items, "total": len(rows)}
