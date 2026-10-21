"""分析控制：启动 / 暂停 / 继续 / 删除，以及撤销导入批次。

三条铁律：

1. **分析永远由用户显式触发** —— 导入不会自动分析；
2. **用户主动暂停 ≠ 执行失败** —— 未执行的内容退回 unprocessed；
3. **删除前必须先结算** —— 不允许在 processing 未定时直接删。

用户层统一叫「暂停分析 / 继续分析」，不暴露 realtime 与 MiMo Batch 的差异。
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, HTTPException, Query

from app import repository as repo
from app.ai import AIError
from app.config import settings
from app.db import get_conn
from app.jobs import cancel_realtime_run, enqueue_analysis
from app.schemas import (
    AnalysisRunOut,
    AnalyzeRequest,
    BatchDeleteRequest,
    RunCancelResult,
)
from app.services import batch
from app.services.errors import NotFoundError

logger = logging.getLogger("content_studio.analysis")

router = APIRouter(prefix="/api", tags=["analysis"])

# 等待分析结算的最长时间与轮询间隔
SETTLE_TIMEOUT_SECONDS = 90
SETTLE_POLL_SECONDS = 0.4


def _serialize_run(conn, row) -> dict:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "import_batch_id": row["import_batch_id"],
        "status": row["status"],
        "total": int(row["total"] or 0),
        "done": int(row["done"] or 0),
        "failed": int(row["failed"] or 0),
        "batch_job_id": row["batch_job_id"],
        "model": row["model"],
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "breakdown": (
            repo.count_content_ids_for_run(conn, row["import_batch_id"])
            if row["import_batch_id"]
            else {}
        ),
    }


async def _start_run(
    import_batch_id: str, mode: str, ids: list[int]
) -> AnalysisRunOut:
    """创建并启动一次分析运行（realtime 或 Batch）。"""
    if mode == "batch":
        if not batch.available():
            raise HTTPException(
                400, "Batch 不可用：未配置 AI_BATCH_BASE_URL，请改用实时分析"
            )
        with get_conn(settings.db_path) as conn:
            run_id = repo.create_analysis_run(
                conn, kind="batch", import_batch_id=import_batch_id,
                total=len(ids), model=settings.model_batch,
            )
            for cid in ids:
                repo.set_analysis_state(conn, cid, "queued")
        try:
            info = await batch.submit_batch(ids)
        except AIError as err:
            # 通道一旦选定就不许悄悄换道：Batch 失败就如实失败。
            message = (
                f"Batch 提交失败：{err}｜未自动切换实时分析；"
                f"如需实时分析，请重新选择「实时 Pro 分析」"
            )
            with get_conn(settings.db_path) as conn:
                repo.update_analysis_run(
                    conn, run_id, status="failed", error=message[:500]
                )
                for cid in ids:
                    repo.set_analysis_state(conn, cid, "unprocessed")
            logger.warning("Batch 提交失败（未回退实时）：run=%s %s", run_id, err)
            raise HTTPException(status_code=502, detail=message) from err
        with get_conn(settings.db_path) as conn:
            repo.update_analysis_run(conn, run_id, batch_job_id=info.get("job_id"))
            run = repo.get_analysis_run(conn, run_id)
            return AnalysisRunOut(**_serialize_run(conn, run))

    with get_conn(settings.db_path) as conn:
        run_id = repo.create_analysis_run(
            conn, kind="realtime", import_batch_id=import_batch_id,
            total=len(ids), model=settings.model_analysis,
        )
    queued = 0
    for cid in ids:
        with get_conn(settings.db_path) as conn:
            repo.set_analysis_state(conn, cid, "queued")
        if enqueue_analysis(cid, run_id):
            queued += 1

    if queued == 0:
        with get_conn(settings.db_path) as conn:
            repo.update_analysis_run(
                conn, run_id, status="failed",
                error="未入队任何内容（AI 密钥可能未配置）",
            )
        raise HTTPException(400, "无法开始实时分析：请先在 backend/.env 配置 AI_API_KEY")

    logger.info("实时分析已启动：run=%s 入队 %s 条", run_id, queued)
    with get_conn(settings.db_path) as conn:
        run = repo.get_analysis_run(conn, run_id)
        return AnalysisRunOut(**_serialize_run(conn, run))


async def _settle_analysis(import_batch_id: str) -> dict[str, int]:
    """等待该批次的分析**结算完成**。

    结算条件：没有活动 run，且没有 queued / processing 的内容。

    删除操作必须建立在结算后的真实状态上 —— 不能用用户点击那一刻的旧数字。
    """
    deadline = time.monotonic() + SETTLE_TIMEOUT_SECONDS
    while True:
        with get_conn(settings.db_path) as conn:
            active = repo.find_active_run(conn, import_batch_id)
            breakdown = repo.batch_analysis_breakdown(conn, import_batch_id)
        pending = breakdown.get("queued", 0) + breakdown.get("processing", 0)
        if active is None and pending == 0:
            return breakdown
        if time.monotonic() >= deadline:
            logger.warning(
                "等待分析结算超时：batch=%s breakdown=%s", import_batch_id, breakdown
            )
            return breakdown
        await asyncio.sleep(SETTLE_POLL_SECONDS)


async def _pause_batch_analysis(import_batch_id: str) -> dict:
    """暂停该批次正在进行的分析。

    realtime 与 Batch 对用户是同一个动作，底层分别走各自的取消机制。
    """
    with get_conn(settings.db_path) as conn:
        active = repo.find_active_run(conn, import_batch_id)

    if active is None:
        return {"paused": False, "kind": None,
                "breakdown": await _settle_analysis(import_batch_id)}

    if active["kind"] == "realtime":
        await cancel_realtime_run(int(active["id"]))
    else:
        job_id = active["batch_job_id"]
        if job_id:
            # 取消失败必须向上抛错，绝不假装已暂停
            await batch.cancel_batch_job(int(job_id))

    breakdown = await _settle_analysis(import_batch_id)
    return {"paused": True, "kind": active["kind"], "breakdown": breakdown}


@router.post("/imports/{import_batch_id}/analyze", response_model=AnalysisRunOut)
async def start_analysis(
    import_batch_id: str, payload: AnalyzeRequest
) -> AnalysisRunOut:
    """用户点击「实时 Pro 分析」或「Batch Pro 分析」后才真正开始。"""
    with get_conn(settings.db_path) as conn:
        if repo.get_import_batch(conn, import_batch_id) is None:
            raise HTTPException(404, f"导入批次 {import_batch_id} 不存在")
        if repo.find_active_run(conn, import_batch_id) is not None:
            raise HTTPException(409, "该批次已有分析在进行中")
        ids = repo.list_content_ids_for_run(conn, import_batch_id, ("unprocessed",))

    if not ids:
        raise HTTPException(400, "该批次没有待分析的内容")

    return await _start_run(import_batch_id, payload.mode, ids)


@router.post("/imports/{import_batch_id}/continue", response_model=AnalysisRunOut)
async def continue_analysis(
    import_batch_id: str, payload: AnalyzeRequest
) -> AnalysisRunOut:
    """继续分析：**只提交仍为 unprocessed 的内容**，并创建新的运行。

    - 不重跑已 analyzed 的内容
    - 不复用已取消的 run 或远端 batch_id
    - total 只反映本次剩余量
    """
    with get_conn(settings.db_path) as conn:
        if repo.get_import_batch(conn, import_batch_id) is None:
            raise HTTPException(404, f"导入批次 {import_batch_id} 不存在")
        if repo.find_active_run(conn, import_batch_id) is not None:
            raise HTTPException(409, "该批次已有分析在进行中，请先暂停或等待完成")
        ids = repo.list_content_ids_for_run(conn, import_batch_id, ("unprocessed",))

    if not ids:
        raise HTTPException(400, "该批次没有待分析的内容")

    logger.info("继续分析：batch=%s 剩余 %s 条", import_batch_id, len(ids))
    return await _start_run(import_batch_id, payload.mode, ids)


@router.post("/imports/{import_batch_id}/pause")
async def pause_analysis(import_batch_id: str) -> dict:
    """暂停分析。暂停只停止继续分析，**不删除任何内容**。"""
    with get_conn(settings.db_path) as conn:
        if repo.get_import_batch(conn, import_batch_id) is None:
            raise HTTPException(404, f"导入批次 {import_batch_id} 不存在")

    try:
        result = await _pause_batch_analysis(import_batch_id)
    except AIError as err:
        raise HTTPException(status_code=502, detail=f"暂停失败：{err}") from err
    return result


@router.post("/imports/{import_batch_id}/delete")
async def delete_batch(import_batch_id: str, payload: BatchDeleteRequest) -> dict:
    """删除批次内容。

    顺序（关键）：
      1. 先暂停并**等待结算**
      2. 基于结算后的真实状态决定删谁
      3. keep_analyzed 只删未完成；all 删除全部
    """
    with get_conn(settings.db_path) as conn:
        if repo.get_import_batch(conn, import_batch_id) is None:
            raise HTTPException(404, f"导入批次 {import_batch_id} 不存在")

    # 1. 暂停并结算
    try:
        await _pause_batch_analysis(import_batch_id)
    except AIError as err:
        raise HTTPException(
            status_code=502, detail=f"暂停失败，已取消删除：{err}"
        ) from err

    # 2. 结算后重新统计（不使用用户点击时的旧数字）
    with get_conn(settings.db_path) as conn:
        breakdown = repo.batch_analysis_breakdown(conn, import_batch_id)
        pending = breakdown.get("queued", 0) + breakdown.get("processing", 0)
        if pending:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"仍有 {pending} 条内容处于未结算状态，"
                    f"为避免误删已取消本次操作，请稍后重试"
                ),
            )

        if payload.mode == "keep_analyzed":
            targets = repo.list_content_ids_for_run(
                conn, import_batch_id, repo.UNFINISHED_STATES
            )
            deleted = repo.delete_contents(conn, targets)
            repo.delete_analysis_runs_for_batch(conn, import_batch_id)
            # 保留批次记录：仍有内容属于它
            keep_batch = True
        else:
            targets = repo.list_content_ids_for_run(
                conn, import_batch_id,
                ("unprocessed", "queued", "processing",
                 "analyzed", "cancelled", "failed"),
            )
            deleted = repo.delete_contents(conn, targets)
            repo.delete_import_batch(conn, import_batch_id)
            repo.delete_analysis_runs_for_batch(conn, import_batch_id)
            keep_batch = False

        remaining = repo.batch_analysis_breakdown(conn, import_batch_id)

    logger.info(
        "删除批次 %s：mode=%s 删除 %s 条，剩余 %s",
        import_batch_id, payload.mode, deleted, remaining,
    )
    return {
        "import_batch_id": import_batch_id,
        "mode": payload.mode,
        "deleted": deleted,
        "kept_analyzed": breakdown.get("analyzed", 0) if payload.mode == "keep_analyzed" else 0,
        "settled_breakdown": breakdown,
        "remaining": remaining,
        "batch_record_kept": keep_batch,
    }


@router.get("/analysis/runs", response_model=list[AnalysisRunOut])
def list_runs(import_batch_id: str | None = Query(default=None)) -> list[AnalysisRunOut]:
    with get_conn(settings.db_path) as conn:
        if import_batch_id:
            rows = conn.execute(
                "SELECT * FROM analysis_runs WHERE import_batch_id = ? ORDER BY id DESC",
                (import_batch_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM analysis_runs ORDER BY id DESC LIMIT 50"
            ).fetchall()
        return [AnalysisRunOut(**_serialize_run(conn, row)) for row in rows]


@router.post("/analysis/runs/{run_id}/cancel", response_model=RunCancelResult)
async def cancel_run(run_id: int) -> RunCancelResult:
    """取消一次分析运行（实时或 Batch）。"""
    with get_conn(settings.db_path) as conn:
        run = repo.get_analysis_run(conn, run_id)
    if run is None:
        raise HTTPException(404, f"分析运行 {run_id} 不存在")

    if run["kind"] == "realtime":
        try:
            result = await cancel_realtime_run(run_id)
        except NotFoundError as err:
            raise HTTPException(404, str(err)) from err
        return RunCancelResult(
            run_id=run_id,
            status=result["status"],
            reverted=int(result.get("reverted", 0)),
            already_finished=bool(result.get("already_finished", False)),
        )

    # Batch：调用远端取消，真正的收尾由 monitor 在轮询到 cancelled 后完成
    job_id = run["batch_job_id"]
    if not job_id:
        with get_conn(settings.db_path) as conn:
            repo.update_analysis_run(conn, run_id, status="cancelled")
        return RunCancelResult(run_id=run_id, status="cancelled")

    try:
        info = await batch.cancel_batch_job(int(job_id))
    except AIError as err:
        raise HTTPException(502, f"取消远端 Batch 失败：{err}") from err

    with get_conn(settings.db_path) as conn:
        repo.update_analysis_run(
            conn, run_id,
            status="cancelled" if info.get("already_finished") else "cancelling",
        )

    return RunCancelResult(
        run_id=run_id,
        status=info["status"],
        remote=bool(info.get("remote", False)),
        batch_id=info.get("batch_id"),
        already_finished=bool(info.get("already_finished", False)),
    )


@router.delete("/imports/{import_batch_id}")
async def undo_import(import_batch_id: str) -> dict:
    """一键撤销整批导入。

    顺序：先取消仍在进行的分析（含远端 Batch），再删除本地内容。
    即使远端取消失败，本地删除仍要继续 —— 不能让用户卡住。
    """
    with get_conn(settings.db_path) as conn:
        if repo.get_import_batch(conn, import_batch_id) is None:
            raise HTTPException(404, f"导入批次 {import_batch_id} 不存在")
        active = repo.find_active_run(conn, import_batch_id)

    cancelled_remote = False
    if active is not None:
        if active["kind"] == "realtime":
            await cancel_realtime_run(int(active["id"]))
        elif active["batch_job_id"]:
            try:
                info = await batch.cancel_batch_job(int(active["batch_job_id"]))
                cancelled_remote = bool(info.get("remote", False))
            except Exception:  # noqa: BLE001 —— 远端失败不阻塞本地清理
                logger.exception("撤销导入时取消远端 Batch 失败：batch=%s", import_batch_id)

    with get_conn(settings.db_path) as conn:
        removed = repo.delete_contents_by_import_batch(conn, import_batch_id)
        repo.delete_import_batch(conn, import_batch_id)
        conn.execute(
            "DELETE FROM analysis_runs WHERE import_batch_id = ?", (import_batch_id,)
        )

    logger.info("已撤销导入批次 %s，删除 %s 条内容", import_batch_id, removed)
    return {
        "import_batch_id": import_batch_id,
        "deleted": removed,
        "cancelled_remote_batch": cancelled_remote,
    }
