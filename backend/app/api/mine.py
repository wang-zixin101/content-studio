"""「我的」V1 的 API（0 AI）。

独立 namespace：`/api/mine`
—— 不复用 /api/imports，不写 import_batches，不进「导入记录」。

这一组端点只做：日期/整数运算、CSV 严格解析、SQL 读写。
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from app.schemas import (
    AccountMetricOut,
    AccountMetricsImportOut,
    GoalCreateIn,
    GoalOut,
    GoalUpdateIn,
    SnapshotCreateIn,
    SnapshotOut,
    SnapshotUpdateIn,
)
from app.services import mine
from app.services.errors import ServiceError

router = APIRouter(prefix="/api/mine", tags=["mine"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB —— 只做保护，不是业务限制


# ---------------------------------------------------------------- 目标

@router.get("/goal", response_model=GoalOut)
def get_goal() -> GoalOut:
    """目标 + 现算进度。没有目标时 exists=False（不是 404 —— 那是常态）。"""
    return GoalOut(**mine.get_goal())


@router.post("/goal", response_model=GoalOut)
def create_goal(payload: GoalCreateIn) -> GoalOut:
    """建立目标。已存在 → 409（只有一个目标，不静默覆盖）。"""
    try:
        return GoalOut(
            **mine.create_goal(
                payload.start_date, payload.baseline_qualified_impressions
            )
        )
    except ServiceError as err:
        raise HTTPException(status_code=err.status_code, detail=err.message) from err


@router.patch("/goal", response_model=GoalOut)
def update_goal(payload: GoalUpdateIn) -> GoalOut:
    """就地修改目标（单例）。

    只能改 start_date 与 baseline —— TARGET / DURATION 是固定规则，
    连请求体里都不存在这两个字段。
    目标不存在 → 404；不新建、不留历史、不碰快照与账号指标。
    """
    try:
        return GoalOut(
            **mine.update_goal(
                payload.start_date, payload.baseline_qualified_impressions
            )
        )
    except ServiceError as err:
        raise HTTPException(status_code=err.status_code, detail=err.message) from err


# ---------------------------------------------------------------- 合格浏览量快照

@router.get("/snapshots", response_model=list[SnapshotOut])
def list_snapshots() -> list[SnapshotOut]:
    return [SnapshotOut(**s) for s in mine.list_snapshots()]


@router.post("/snapshots", response_model=SnapshotOut)
def add_snapshot(payload: SnapshotCreateIn) -> SnapshotOut:
    try:
        return SnapshotOut(
            **mine.add_snapshot(payload.recorded_at, payload.qualified_impressions)
        )
    except ServiceError as err:
        raise HTTPException(status_code=err.status_code, detail=err.message) from err


@router.patch("/snapshots/{snapshot_id}", response_model=SnapshotOut)
def edit_snapshot(snapshot_id: int, payload: SnapshotUpdateIn) -> SnapshotOut:
    try:
        return SnapshotOut(
            **mine.edit_snapshot(
                snapshot_id, payload.recorded_at, payload.qualified_impressions
            )
        )
    except ServiceError as err:
        raise HTTPException(status_code=err.status_code, detail=err.message) from err


@router.delete("/snapshots/{snapshot_id}")
def remove_snapshot(snapshot_id: int) -> dict:
    try:
        mine.remove_snapshot(snapshot_id)
    except ServiceError as err:
        raise HTTPException(status_code=err.status_code, detail=err.message) from err
    return {"deleted": 1, "id": snapshot_id}


# ---------------------------------------------------------------- 账号日指标

@router.post("/account-metrics/import", response_model=AccountMetricsImportOut)
async def import_account_metrics(
    file: UploadFile = File(...),
) -> AccountMetricsImportOut:
    """导入 Account Overview CSV。

    **整文件一个事务**：任一行解析失败 → 全部回滚，库里不留半成品。
    已存在的日期 → 整行覆盖（不是合并、不是累加）。
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大（上限 {MAX_UPLOAD_BYTES // 1024 // 1024} MB）",
        )

    try:
        result = mine.import_account_metrics(data)
    except mine.AccountMetricsCsvError as err:
        detail: dict[str, object] = {"message": err.message}
        if err.row is not None:
            detail["row"] = err.row
        if err.column is not None:
            detail["column"] = err.column
        raise HTTPException(status_code=400, detail=detail) from err
    except ServiceError as err:
        raise HTTPException(status_code=err.status_code, detail=err.message) from err

    return AccountMetricsImportOut(**result)


@router.get("/account-metrics", response_model=AccountMetricOut)
def get_account_metric(
    metric: str = Query(default="impressions"),
) -> AccountMetricOut:
    """某指标的完整序列（不分页）+ 最新 / 上一次 / 差值。

    metric 走白名单；指标列名在 repository 里**模块加载时固化**，
    用户输入永远不会进入 SQL 文本。
    """
    try:
        return AccountMetricOut(**mine.query_account_metric(metric))
    except ServiceError as err:
        raise HTTPException(status_code=err.status_code, detail=err.message) from err
