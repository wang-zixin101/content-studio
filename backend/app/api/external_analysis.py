"""外部分析导入 V1 的 API（Stage 12）+ V2 任务 02（按作者不限量导出）。

**独立 namespace**：不复用 /api/imports，不进 import_batches，
不出现在「导入记录」，也不获得「撤销内容导入」语义。

全部 0 AI —— 这些端点只做 SQL、字符串拼接、严格校验与事务写入。

------------------------------------------------------------------
V2 任务 02 的三处语义变更（都保持向后兼容）
------------------------------------------------------------------
1) 导出数量：**取消 20/50/100 上限**。
   limit 参数保留（0 = 不限量，默认；正整数仍截断），
   老链接 / 脚本带 limit=50 不会 500，只是仍然截断。
2) 作者范围：新增 `author_ids`（逗号分隔，与 /performance-insights
   同一个范式）。「全部作者」= 不传任何作者参数（天然包含无作者内容）；
   「指定作者」= 只取这些 author_id。
3) 结果上传上限 20 MB → 200 MB，并且**不再整体读进内存**：
   直接复用 Starlette 已经 spool 好的上传流，逐行解析。
"""

from __future__ import annotations

import os
from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from app import repository as repo
from app.config import (
    ANALYSIS_PROMPT_VERSION,
    ANALYSIS_SCHEMA_VERSION,
    EXTERNAL_ANALYSIS_FORMAT_VERSION,
    settings,
)
from app.db import get_conn
from app.prompts import build_external_analysis_prompt
from app.schemas import (
    ExternalConfirmOut,
    ExternalHandoffCandidatesOut,
    ExternalHandoffIn,
    ExternalHandoffOut,
    ExternalPreviewOut,
    ExternalTaskPreviewOut,
)
from app.services import external_analysis as ext
from app.services.analysis_spec import ANALYSIS_SCHEMA_HASH
from app.services.errors import ServiceError

router = APIRouter(prefix="/api/external-analysis", tags=["external-analysis"])

# 200 MB —— 取消导出上限后，结果文件必须能一次导回来。
# 实测典型结果行约 1.85 KB → 200 MB ≈ 10 万条结果，远大于当前语料规模；
# 再大就明确报 413，绝不静默丢弃有效结果。
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


def _parse_states(
    scope: str | None,
    states: list[str] | None,
) -> tuple[str, ...]:
    """统一的状态解析：scope 优先，其次显式 states，都没给走默认。"""
    return ext.resolve_export_states(scope, states)


def _parse_author_ids(author_ids: str | None) -> list[int]:
    """逗号分隔的正式 author_id（与 /performance-insights 同范式）。"""
    if not author_ids:
        return []
    raw = author_ids.strip()
    if not raw:
        return []
    try:
        parsed = [int(p.strip()) for p in raw.split(",") if p.strip()]
    except ValueError as err:
        raise HTTPException(
            status_code=400, detail="author_ids 必须是逗号分隔的整数"
        ) from err
    if not parsed:
        raise HTTPException(status_code=400, detail="author_ids 不能为空")
    return parsed


def _validated_author_ids(author_ids: list[int]) -> list[int]:
    """作者必须真实存在 —— 拼错 id 静默导出 0 条是最难排查的问题。"""
    if not author_ids:
        return []
    with get_conn(settings.db_path) as conn:
        for aid in author_ids:
            if repo.get_author(conn, aid) is None:
                raise HTTPException(status_code=404, detail=f"作者 {aid} 不存在")
    return author_ids


def _open_upload(file: UploadFile) -> tuple[Any, int]:
    """拿到上传流与大小，**不把内容读进内存**。

    Starlette 的 UploadFile 本身就是一个 SpooledTemporaryFile：
    小文件在内存、大文件已经落在临时磁盘上。之前 `await file.read()`
    会把它**整体**读成 bytes，再 decode 成 str，再 split 成行列表 ——
    峰值约 3 倍文件大小。现在直接复用这个流逐行解析，
    内存占用与文件条数无关。

    大小上限在这里**一次性**判定：超限立即 413，不做任何读取。
    """
    fp = file.file
    try:
        fp.seek(0, os.SEEK_END)
        size = fp.tell()
        fp.seek(0)
    except (AttributeError, OSError) as err:  # pragma: no cover
        raise HTTPException(status_code=400, detail="无法读取上传文件") from err

    if size == 0:
        raise HTTPException(status_code=400, detail="文件为空")
    if size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"文件过大：上限 {MAX_UPLOAD_BYTES // 1024 // 1024} MB，"
                f"实际 {size / 1024 / 1024:.1f} MB。请按作者分批导出后再导入。"
            ),
        )
    return fp, size


@router.get("/prompt")
def get_external_prompt(count: int = Query(default=0, ge=0)) -> dict:
    """返回可直接复制给外部模型的提示词。**0 AI**。

    提示词里的字段定义直接引用 ANALYSIS_SYSTEM + ANALYSIS_SCHEMA_HINT ——
    全项目只有一份 ANALYSIS 定义，不存在第二份 schema。
    """
    text = build_external_analysis_prompt(
        schema_version=ANALYSIS_SCHEMA_VERSION,
        schema_hash=ANALYSIS_SCHEMA_HASH,
        prompt_version=ANALYSIS_PROMPT_VERSION,
        format_version=EXTERNAL_ANALYSIS_FORMAT_VERSION,
        task_count=count,
    )
    return {
        "prompt": text,
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "schema_hash": ANALYSIS_SCHEMA_HASH,
        "prompt_version": ANALYSIS_PROMPT_VERSION,
        "format_version": EXTERNAL_ANALYSIS_FORMAT_VERSION,
    }


@router.get("/tasks/preview", response_model=ExternalTaskPreviewOut)
def preview_tasks(
    source: Literal["mine", "benchmark"] | None = None,
    author_id: int | None = None,
    author_ids: str | None = Query(
        None, description="可选：正式 author_id，逗号分隔，如 1,2,3；不传 = 全部作者"
    ),
    include_no_author: bool = Query(
        False, description="指定了作者时，是否同时包含无作者的内容"
    ),
    scope: Literal["pending", "all"] | None = Query(
        None,
        description="pending=仅待分析（未分析+失败）；all=全部可导出（含已分析/已取消）",
    ),
    states: list[str] | None = Query(default=None),
    limit: int = Query(default=ext.EXPORT_DEFAULT_LIMIT, ge=0),
) -> ExternalTaskPreviewOut:
    """导出前的**真实**范围统计（纯查询，0 写入 / 0 AI）。

    `count` 与 `matching_count` 都取自**不带上限**的真实计数：
    预览显示多少条，点下载就得到多少条。
    """
    resolved = _parse_states(scope, states)
    ids = _validated_author_ids(_parse_author_ids(author_ids))

    summary = ext.export_scope_summary(
        source=source,
        author_id=author_id,
        author_ids=ids,
        include_no_author=include_no_author,
        states=resolved,
    )
    matching = summary["matching_count"]
    return ExternalTaskPreviewOut(
        count=matching,
        limit=limit,
        states=list(resolved),
        source=source,
        author_id=author_id,
        state_counts=summary["state_counts"],
        author_ids=ids,
        include_no_author=include_no_author,
        scope=scope,
        matching_count=matching,
        author_count=summary["author_count"],
        no_author_count=summary["no_author_count"],
        estimated_bytes=summary["estimated_bytes"],
    )


@router.get("/tasks/export")
def export_tasks(
    source: Literal["mine", "benchmark"] | None = None,
    author_id: int | None = None,
    author_ids: str | None = Query(
        None, description="可选：正式 author_id，逗号分隔，如 1,2,3；不传 = 全部作者"
    ),
    include_no_author: bool = Query(
        False, description="指定了作者时，是否同时包含无作者的内容"
    ),
    scope: Literal["pending", "all"] | None = Query(
        None, description="pending=仅待分析；all=全部可导出"
    ),
    states: list[str] | None = Query(default=None),
    limit: int = Query(default=ext.EXPORT_DEFAULT_LIMIT, ge=0),
) -> Response:
    """下载任务 JSONL。**后端生成**，前端不拼数据。UTF-8。

    **与 /tasks/preview 使用完全相同的筛选参数**：预览显示 N 条 →
    这里就导出 N 条，绝不静默截断（queued / processing 会被明确拒绝，
    而不是悄悄少给）。
    """
    resolved = _parse_states(scope, states)
    ids = _validated_author_ids(_parse_author_ids(author_ids))

    targets = ext.select_export_targets(
        source=source,
        author_id=author_id,
        author_ids=ids,
        include_no_author=include_no_author,
        states=resolved,
        limit=limit,
    )
    if not targets:
        raise ServiceError("当前筛选条件下没有可导出的内容")

    payload = ext.build_task_jsonl(targets)
    filename = ext.task_filename()
    return Response(
        content=payload,
        media_type="application/x-ndjson; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # 让调用方能自检「下载条数 == 数据库条数」
            "X-Export-Count": str(len(targets)),
        },
    )


@router.post("/results/preview", response_model=ExternalPreviewOut)
async def preview_results(
    file: UploadFile = File(...),
    overwrite: bool = Form(default=False),
) -> ExternalPreviewOut:
    """预检结果文件。**0 DB 写入 / 0 AI**；文件级错误只标 file_error，不抛 500。

    大文件走流式逐行解析 —— 不整体读进内存。
    """
    stream, _size = _open_upload(file)

    try:
        result = ext.preview_result_file(overwrite=overwrite, stream=stream)
    except ext.FileLevelError as err:
        return ExternalPreviewOut(file_error=str(err), overwrite=overwrite)

    return ExternalPreviewOut(
        meta=result["meta"],
        warnings=result["warnings"],
        counts=result["counts"],
        total=result["total"],
        valid_count=result["valid_count"],
        items=result["items"],
        overwrite=overwrite,
        confirm_token=result["confirm_token"],
    )


@router.post("/results/confirm", response_model=ExternalConfirmOut)
async def confirm_results(
    file: UploadFile = File(...),
    overwrite: bool = Form(default=False),
    token: str | None = Form(default=None),
) -> ExternalConfirmOut:
    """确认导入：整批一个事务，全有或全无。**0 AI**。

    大文件同样走流式；标签统计延迟到整批结束统一做一次（性能）。
    """
    stream, _size = _open_upload(file)

    try:
        result = ext.confirm_import(overwrite=overwrite, token=token, stream=stream)
    except ext.FileLevelError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err

    return ExternalConfirmOut(**result)


@router.get("/handoff/candidates", response_model=ExternalHandoffCandidatesOut)
def handoff_candidates(
    source: Literal["mine", "benchmark"] | None = None,
    author_id: int | None = None,
    author_ids: str | None = Query(None, description="可选：逗号分隔的正式 author_id"),
    include_no_author: bool = Query(False),
    limit: int = Query(default=ext.EXPORT_DEFAULT_LIMIT, ge=0),
) -> ExternalHandoffCandidatesOut:
    """当前筛选下待转出的 queued 内容 id。**0 写入 / 0 AI**。"""
    ids = _validated_author_ids(_parse_author_ids(author_ids))
    content_ids = ext.queued_handoff_candidates(
        source=source,
        author_id=author_id,
        author_ids=ids,
        include_no_author=include_no_author,
        limit=limit,
    )
    return ExternalHandoffCandidatesOut(count=len(content_ids), ids=content_ids)


@router.post("/handoff", response_model=ExternalHandoffOut)
def handoff(payload: ExternalHandoffIn) -> ExternalHandoffOut:
    """把 queued 的内容交接给外部分析（Stage 12.1）。

    只转 `queued` → `unprocessed`；`processing` 明确拒绝（不强杀在飞请求）。
    单事务 + CAS，与 worker 的认领竞争只有一个赢家。**0 AI**。
    """
    result = ext.handoff_to_external(payload.content_ids)
    return ExternalHandoffOut(**result)
