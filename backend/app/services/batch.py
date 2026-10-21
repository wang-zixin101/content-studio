"""批量分析（Batch API）。

>= BATCH_THRESHOLD 条导入时默认走 Batch Pro；用户可强制改用实时分析。

严格约束：
- Batch 使用**独立端点**（AI_BATCH_BASE_URL）。未配置该端点时 Batch 视为不可用，
  绝不退回到实时端点；
- Batch 与实时分析**共用** build_analysis_messages / 合同校验 / 保存路径，
  且同样要求结构化 JSON 输出（response_format=json_object）；
- Batch 只用于「批量导入后的内容分析」，不用于生成初稿 / 改写 / 风格档案；
- 任务持久化在 SQLite，重启后恢复并持续监控，且**禁止重复提交**；
- 官方结果分两个文件：成功在 output_file_id，失败在 error_file_id。

注意：本模块基于「MiMo 兼容 OpenAI 协议」实现，尚未经过真实调用验证。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app import repository as repo
from app.ai import AIError, extract_json
from app.ai.factory import get_batch_provider
from app.config import (
    ANALYSIS_DETECT_TRUNCATION,
    ANALYSIS_PROMPT_VERSION,
    BATCH_THRESHOLD,
    settings,
)
from app.db import get_conn
from app.prompts import build_analysis_messages
from app.services.analysis_spec import SpecError, validate_analysis
from app.services.analyzer import TRUNCATED_FINISH_REASONS, compute_length_chars
from app.services.persistence import persist_analysis_result

logger = logging.getLogger("content_studio.batch")

TERMINAL_STATUSES = {"completed", "failed", "expired", "cancelled"}

# 常驻监控的轮询间隔（秒）
MONITOR_INTERVAL_SECONDS = 45


def available() -> bool:
    """Batch 是否可用：必须已配置独立的 Batch Base URL。"""
    return settings.batch_configured and get_batch_provider() is not None


def should_use_batch(count: int) -> bool:
    """是否默认走 Batch（达到阈值且 Batch 可用）。"""
    return count >= BATCH_THRESHOLD and available()


def build_jsonl(items: list[tuple[int, str]]) -> tuple[bytes, dict[str, int]]:
    """把 (content_id, raw_text) 列表编成 Batch JSONL。

    - messages 复用 build_analysis_messages —— 与实时分析完全同一套 Prompt；
    - **不发送 max_completion_tokens / max_tokens** —— 与实时链一致，
      使用供应商默认上限（mimo-v2.6-pro 为 131072）。
      两条通道的输出预算因此永远相同，不会各写各的。
    """
    lines: list[str] = []
    custom_map: dict[str, int] = {}
    for content_id, raw_text in items:
        custom_id = f"c{content_id}"
        custom_map[custom_id] = content_id
        body = {
            "custom_id": custom_id,
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": settings.model_batch,
                "messages": build_analysis_messages(raw_text),
                "response_format": {"type": "json_object"},
            },
        }
        lines.append(json.dumps(body, ensure_ascii=False))
    payload = ("\n".join(lines) + "\n").encode("utf-8") if lines else b""
    return payload, custom_map


# ---------------------------------------------------------------- 提交

async def submit_batch(content_ids: list[int]) -> dict[str, Any]:
    """提交一批内容做 Batch 分析。

    Batch 不可用或提交失败时**直接抛错**。

    **严禁**由调用方静默回退到实时分析 —— 用户点击的是「Batch Pro 分析」，
    通道就必须是 Batch。失败就如实失败，把选择权交回用户。
    """
    if not content_ids:
        return {"submitted": 0, "content_ids": []}

    provider = get_batch_provider()
    if provider is None:
        raise AIError(
            "未配置 AI_BATCH_BASE_URL，Batch 不可用（请从 MiMo Batch Inference 页面获取端点）",
            retryable=False,
        )

    with get_conn(settings.db_path) as conn:
        items: list[tuple[int, str]] = []
        for cid in content_ids:
            row = repo.get_content_raw(conn, cid)
            if row is not None:
                items.append((cid, row["raw_text"]))

    if not items:
        return {"submitted": 0, "content_ids": []}

    payload, custom_map = build_jsonl(items)

    with get_conn(settings.db_path) as conn:
        job_id = repo.create_batch_job(
            conn,
            model=settings.model_batch,
            batch_id=None,
            input_file_id=None,
            status="pending",
            custom_map=custom_map,
            total=len(items),
            prompt_version=ANALYSIS_PROMPT_VERSION,
        )

    try:
        file_id = await provider.upload_batch_file("analysis.jsonl", payload)
        batch = await provider.create_batch(file_id)
        batch_id = str(batch.get("id") or "")
        status = str(batch.get("status") or "submitted")
    except AIError as err:
        with get_conn(settings.db_path) as conn:
            repo.update_batch_job(conn, job_id, status="failed", error=str(err)[:500])
        raise

    with get_conn(settings.db_path) as conn:
        repo.update_batch_job(
            conn, job_id,
            batch_id=batch_id, input_file_id=file_id, status=status,
        )

    logger.info("Batch 已提交：job=%s batch=%s 条数=%s", job_id, batch_id, len(items))
    return {
        "job_id": job_id,
        "batch_id": batch_id,
        "submitted": len(items),
        "content_ids": [cid for cid, _ in items],
    }


# ---------------------------------------------------------------- 结果写回

def _apply_success(
    output_text: str, custom_map: dict[str, int]
) -> tuple[int, int, set[int], int]:
    """处理成功文件（output_file_id）。

    返回 (成功数, 失败数, 已处理内容 id 集合, 其中 mine 内容条数)。
    """
    succeeded = 0
    failed = 0
    resolved: set[int] = set()
    mine_count = 0

    for line in output_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        content_id = custom_map.get(str(record.get("custom_id")))
        if content_id is None:
            continue

        response = record.get("response") or {}
        status_code = int(response.get("status_code") or 200)

        if record.get("error") or status_code >= 400:
            resolved.add(content_id)
            failed += 1
            _mark_failed(content_id, f"Batch 条目失败：{str(record.get('error') or status_code)}")
            continue

        try:
            choice = response["body"]["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
            # 截断检测与实时路径同口径：Batch 也是同一个推理模型，
            # 撞到服务端输出上限时 content 同样是半截 JSON。
            if ANALYSIS_DETECT_TRUNCATION and (
                finish_reason in TRUNCATED_FINISH_REASONS
            ):
                resolved.add(content_id)
                failed += 1
                _mark_failed(
                    content_id,
                    f"Batch 输出被截断（finish_reason={finish_reason}）；"
                    f"请求未指定 max_completion_tokens，用的是供应商默认上限",
                )
                continue

            data = extract_json(content)
            resolved.add(content_id)
            outcome = _save_analysis(content_id, data)
            if outcome is None:
                continue          # 内容已被删除：安全跳过，不计入成功
            if outcome == "mine":
                mine_count += 1
            succeeded += 1
        except SpecError as err:
            # 合同不符：明确标记失败，**绝不以 analyzed 身份落库**
            resolved.add(content_id)
            failed += 1
            logger.warning("Batch 条目不符合合同 content_id=%s：%s", content_id, err)
            _mark_failed(content_id, f"Batch 结果不符合 ANALYSIS 合同：{err}")
        except Exception:  # noqa: BLE001 —— 单条失败不影响整批
            resolved.add(content_id)
            failed += 1
            logger.exception("Batch 条目解析失败 content_id=%s", content_id)
            _mark_failed(content_id, "Batch 结果解析失败")

    return succeeded, failed, resolved, mine_count


def _apply_errors(error_text: str, custom_map: dict[str, int]) -> tuple[int, set[int]]:
    """处理失败文件（error_file_id）。返回 (失败数, 已处理内容 id 集合)。

    MiMo 官方错误条目形如：
        {"custom_id": "request-x", "response": null,
         "error": {"code": "...", "message": "..."}}
    """
    failed = 0
    resolved: set[int] = set()

    for line in error_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        content_id = custom_map.get(str(record.get("custom_id")))
        if content_id is None:
            continue

        error = record.get("error") or {}
        code = error.get("code") or "batch_error"
        message = error.get("message") or "批量请求失败"
        resolved.add(content_id)
        failed += 1
        _mark_failed(content_id, f"Batch 失败 [{code}] {message}")

    return failed, resolved


def _terminal_reason(status: str, had_result_file: bool) -> str:
    """终态未返回结果时的失败原因。"""
    if status == "completed":
        return "Batch 已完成但未返回对应结果"
    return f"Batch {status}"


def _mark_failed(content_id: int, reason: str) -> None:
    with get_conn(settings.db_path) as conn:
        repo.set_status(conn, content_id, "failed", reason[:500])


def _save_analysis(content_id: int, data: dict[str, Any]) -> str | None:
    """与实时分析**完全相同的保存路径**，包括同一套严格合同校验。

    返回 'mine'（mine 内容）/ 'ok'（其他来源）；
    内容已被删除时返回 None —— 调用方应安全跳过，且不计入成功。
    合同不符时**抛 SpecError**，由调用方标记失败而不是静默接受。
    """
    validate_analysis(data)
    with get_conn(settings.db_path) as conn:
        raw = repo.get_content_raw(conn, content_id)
        if raw is None:
            logger.info("Batch 结果对应的内容 %s 已不存在，跳过", content_id)
            return None
        is_mine = raw["source"] == "mine"
        # 与 realtime / 外部分析导入**完全同一套**落库副作用
        persist_analysis_result(
            conn,
            content_id,
            data,
            raw=data,
            model=settings.model_batch,
            raw_text=raw["raw_text"],
        )
        # length_chars 与本机计算口径一致（不由模型生成）
        conn.execute(
            "UPDATE analyses SET length_chars = ? WHERE content_id = ?",
            (compute_length_chars(raw["raw_text"]), int(content_id)),
        )
        return "mine" if is_mine else "ok"


# ---------------------------------------------------------------- 轮询

async def poll_batch(job_id: int) -> dict[str, Any]:
    """查询并处理一个 Batch 任务（只查询，不提交）。"""
    with get_conn(settings.db_path) as conn:
        job = repo.get_batch_job(conn, job_id)
    if job is None:
        return {"job_id": job_id, "status": "unknown"}

    if job["status"] in TERMINAL_STATUSES:
        return {"job_id": job_id, "status": job["status"], "skipped": True}

    batch_id = job["batch_id"]
    if not batch_id:
        return {"job_id": job_id, "status": job["status"], "skipped": True}

    provider = get_batch_provider()
    if provider is None:
        return {"job_id": job_id, "status": job["status"], "skipped": True}

    batch = await provider.get_batch(batch_id)
    status = str(batch.get("status") or "unknown")

    # 非终态（validating / in_progress / finalizing…）：只更新状态，等下一轮 monitor
    if status not in TERMINAL_STATUSES:
        with get_conn(settings.db_path) as conn:
            repo.update_batch_job(conn, job_id, status=status)
        return {"job_id": job_id, "status": status}

    # ---- 终态：先处理已返回的结果，再收尾未解决的条目 ----
    custom_map = json.loads(job["custom_map"] or "{}")
    output_file_id = batch.get("output_file_id")
    error_file_id = batch.get("error_file_id")

    succeeded = failed = reverted = 0
    resolved: set[int] = set()
    mine_analyzed = 0

    if output_file_id:
        output_text = await provider.download_file(str(output_file_id))
        s, f, ids, mine_n = _apply_success(output_text, custom_map)
        succeeded += s
        failed += f
        resolved |= ids
        mine_analyzed += mine_n

    if error_file_id:
        error_text = await provider.download_file(str(error_file_id))
        f, ids = _apply_errors(error_text, custom_map)
        failed += f
        resolved |= ids

    # 仍未拿到结果的条目绝不能永久卡在 pending
    had_result_file = bool(output_file_id) or bool(error_file_id)
    for content_id in custom_map.values():
        if content_id in resolved:
            continue
        with get_conn(settings.db_path) as conn:
            row = repo.get_content_raw(conn, content_id)
            if row is None or row["status"] == "analyzed":
                continue  # 已删除或已有结果：不覆盖
            if status == "cancelled":
                # 用户主动取消导致未执行 —— 这不是失败，退回「未分析」
                repo.set_analysis_status(conn, content_id, "unprocessed")
                reverted += 1
            else:
                repo.set_status(conn, content_id, "failed",
                                _terminal_reason(status, had_result_file))
                failed += 1

    with get_conn(settings.db_path) as conn:
        repo.update_batch_job(
            conn, job_id,
            status=status,
            output_file_id=str(output_file_id) if output_file_id else None,
            succeeded=succeeded,
            failed=failed,
        )
        # 同步关联的 analysis run（若有）
        run = conn.execute(
            "SELECT id FROM analysis_runs WHERE batch_job_id = ? "
            "  AND status IN ('running', 'cancelling') ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if run is not None:
            repo.update_analysis_run(
                conn, int(run["id"]),
                status="cancelled" if status == "cancelled" else
                       ("completed" if status == "completed" else "failed"),
                done=succeeded + failed,
                failed=failed,
            )

    logger.info(
        "Batch 终态 %s：job=%s 成功=%s 失败=%s 退回未分析=%s",
        status, job_id, succeeded, failed, reverted,
    )

    # mine 内容成功写回后，走与实时完全相同的风格更新入口（整批只触发一次）
    if mine_analyzed:
        from app.jobs import notify_analysis_completed

        notify_analysis_completed()

    return {
        "job_id": job_id, "status": status,
        "succeeded": succeeded, "failed": failed, "reverted": reverted,
    }


async def cancel_batch_job(job_id: int) -> dict[str, Any]:
    """请求取消一个 Batch 任务。

    MiMo 的取消是异步的：这里只把 job 置为 cancelling 并调用 cancel endpoint，
    真正的收尾（成功结果保留、未执行部分退回未分析）由 monitor 在轮询到
    cancelled 之后完成。
    """
    with get_conn(settings.db_path) as conn:
        job = repo.get_batch_job(conn, job_id)
        if job is None:
            raise AIError(f"Batch 任务 {job_id} 不存在", retryable=False)
        if job["status"] in TERMINAL_STATUSES:
            return {"job_id": job_id, "status": job["status"],
                    "already_finished": True}
        batch_id = job["batch_id"]
        repo.update_batch_job(conn, job_id, status="cancelling")

    if not batch_id:
        # 还没提交到远端（例如提交就失败了）：本地直接终态
        with get_conn(settings.db_path) as conn:
            repo.update_batch_job(conn, job_id, status="cancelled")
        return {"job_id": job_id, "status": "cancelled", "remote": False}

    provider = get_batch_provider()
    if provider is None:
        raise AIError("未配置 AI_BATCH_BASE_URL，无法取消远端 Batch", retryable=False)

    await provider.cancel_batch(str(batch_id))
    logger.info("已请求取消远端 Batch：job=%s batch=%s", job_id, batch_id)
    return {"job_id": job_id, "status": "cancelling", "remote": True,
            "batch_id": batch_id}


async def recover_batches() -> list[dict[str, Any]]:
    """启动时恢复：查询一次所有未完成的 Batch（不重复提交）。"""
    with get_conn(settings.db_path) as conn:
        jobs = repo.list_unfinished_batches(conn)

    results = []
    for job in jobs:
        try:
            results.append(await poll_batch(int(job["id"])))
        except Exception:  # noqa: BLE001
            logger.exception("恢复 Batch 失败 job=%s", job["id"])
    if jobs:
        logger.info("已恢复查询 %s 个未完成 Batch 任务", len(jobs))
    return results


async def monitor_loop() -> None:
    """常驻 Batch 监控：持续自动发现完成状态并写回。

    设计要点：
    - 每轮扫描数据库中的未完成 Batch，逐个 poll；用户不重启工作台也能拿到结果；
    - 没有任务时同样按固定间隔休眠，不会忙循环；
    - 单条 poll 失败只记录日志，绝不打断整个监控；
    - 全程只查询不提交，因此不存在重复提交。
    """
    logger.info("Batch 监控已启动（间隔 %s 秒）", MONITOR_INTERVAL_SECONDS)
    while True:
        try:
            with get_conn(settings.db_path) as conn:
                jobs = repo.list_unfinished_batches(conn)

            for job in jobs:
                try:
                    await poll_batch(int(job["id"]))
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    logger.exception("Batch 轮询失败 job=%s", job["id"])
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 —— 任何异常都不能杀死监控
            logger.exception("Batch 监控循环异常")

        await asyncio.sleep(MONITOR_INTERVAL_SECONDS)
