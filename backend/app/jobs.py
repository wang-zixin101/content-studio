"""后台分析任务队列。

职责：
- 保存 / 导入后把内容入队，由常驻 worker 异步分析（不阻塞请求）。
- 服务启动时恢复数据库里未完成的 pending / analyzing 任务。
- 同一 content_id 不重复入队。
- 可恢复错误（超时 / 429 / 5xx）有限重试 + 指数退避；不可恢复才标记 failed。
- 每新增 5 条「已成功分析」的我的内容，自动触发一次风格档案重算。

保持纯 asyncio 实现，不引入 Redis / Celery 等外部依赖。
"""

from __future__ import annotations

import asyncio
import logging

from app import repository as repo
from app.ai import AIError
from app.config import settings
from app.db import get_conn
from app.services.analyzer import analyze_content

logger = logging.getLogger("content_studio.jobs")

WORKER_COUNT = 2            # 常驻 worker 数
MAX_RETRIES = 3             # 可恢复错误的最大重试次数
BASE_BACKOFF = 2.0          # 退避基数（秒）
MAX_BACKOFF = 30.0          # 退避上限（秒）
MAX_STYLE_CATCHUP = 5       # 单轮风格档案更新的最大补齐次数（防无限循环）
# 风格档案的自动更新阈值集中在 services.style_profile（STYLE_REFRESH_EVERY），
# 这里通过 profile_staleness() 的 should_recompute 统一判断，避免两处漂移。

_queue: asyncio.Queue[tuple[int, int | None]] | None = None
_workers: list[asyncio.Task] = []
# 队列未启动时的暂存区，元素为 (content_id, run_id)
_pending: list[tuple[int, int | None]] = []
_inflight: set[int] = set()     # 已入队 / 处理中的 content_id，用于去重
_style_lock: asyncio.Lock | None = None
_style_task: asyncio.Task | None = None   # 正在进行的风格档案更新任务
_style_continue: bool = False             # 当前任务结束后是否需要自动续跑

# _run_one 的结果三态：成功 / 因取消而跳过 / 执行失败。
# 用户主动取消绝不能计入 failed。
RESULT_OK = "ok"
RESULT_SKIPPED = "skipped"
RESULT_FAILED = "failed"


# ---------------------------------------------------------------- 入队

def enqueue_analysis(content_id: int, run_id: int | None = None) -> bool:
    """把内容加入分析队列。重复入队会被忽略，返回是否真正入队。

    未配置 AI 密钥时**不入队**：内容保持未分析状态。绝不允许把「暂时没有能力
    分析」当成「分析失败」——用户配置密钥并重启后，由启动恢复机制统一处理。

    run_id 用于把内容归属到某一次分析运行，使该运行可被用户取消。
    """
    if not settings.ai_configured:
        logger.info(
            "AI 未配置：content_id=%s 保持未分析，配置密钥并重启后会自动分析",
            content_id,
        )
        return False

    if content_id in _inflight:
        logger.debug("跳过重复入队 content_id=%s", content_id)
        return False

    _inflight.add(content_id)
    item = (content_id, run_id)
    if _queue is None:
        _pending.append(item)
    else:
        _queue.put_nowait(item)
    return True


# ---------------------------------------------------------------- 取消辅助

def _run_is_cancelled(run_id: int) -> bool:
    with get_conn(settings.db_path) as conn:
        return repo.is_run_cancelled(conn, run_id)


def _revert_to_unprocessed(content_id: int) -> None:
    """把内容退回「未分析」。内容已被删除时安全跳过。

    走唯一状态入口：analysis_state 与 status 一次写完，不会出现半更新。
    """
    with get_conn(settings.db_path) as conn:
        row = repo.get_content_raw(conn, content_id)
        if row is None:
            return                      # 内容已被删除：跳过
        if row["status"] == "analyzed":
            return                      # 已有结果：不覆盖
        repo.set_analysis_status(conn, content_id, "unprocessed")


def _bump_run_progress(run_id: int, result: str) -> None:
    with get_conn(settings.db_path) as conn:
        run = repo.get_analysis_run(conn, run_id)
        if run is None:
            return
        done = int(run["done"]) + (0 if result == RESULT_SKIPPED else 1)
        failed = int(run["failed"]) + (1 if result == RESULT_FAILED else 0)
        repo.update_analysis_run(conn, run_id, done=done, failed=failed)


# ---------------------------------------------------------------- 执行

def _mark_failed(content_id: int, reason: str) -> None:
    with get_conn(settings.db_path) as conn:
        if repo.get_content_raw(conn, content_id) is None:
            return                      # 内容已被删除：安全跳过
        repo.set_status(conn, content_id, "failed", reason[:500])


async def _run_one(content_id: int, name: str, run_id: int | None = None) -> str:
    """执行一次分析，返回 RESULT_OK / RESULT_SKIPPED / RESULT_FAILED。

    run_id 非空时，每轮开始前都要检查该运行是否已被取消 ——
    用户取消后不得再发起任何新的模型调用。
    """
    if run_id is not None and _run_is_cancelled(run_id):
        _revert_to_unprocessed(content_id)
        logger.info("[%s] 运行 %s 已取消，跳过 content_id=%s", name, run_id, content_id)
        return RESULT_SKIPPED

    # 内容可能在排队期间被删除
    with get_conn(settings.db_path) as conn:
        if repo.get_content_raw(conn, content_id) is None:
            logger.info("[%s] content_id=%s 已不存在，跳过", name, content_id)
            return RESULT_SKIPPED

    if not settings.ai_configured:
        # 防御：密钥可能在运行期被移除或从未配置。保持未分析，
        # 不调用 AI、不标记 failed。
        _revert_to_unprocessed(content_id)
        logger.warning("[%s] AI 未配置，content_id=%s 保持未分析", name, content_id)
        return RESULT_SKIPPED

    # ------------------------------------------------------------------
    # pre-flight 状态守卫（Stage 12.1）
    # ------------------------------------------------------------------
    # 队列 item 一旦入队就无法安全删除，因此「用户把内容转为外部分析」靠状态交接实现：
    #     queued → unprocessed
    # 但旧 item 仍在 asyncio.Queue 里。取到它时，必须在**调用模型之前**重新确认状态，
    # 否则用户刚交接出去的内容会被内部 worker 抢跑（正是这一轮要修的 P0）。
    #
    # claim_for_analysis 是单条 CAS：只有仍是 queued / processing 的行才会被推进到
    # processing。handoff 之后是 unprocessed → 直接 skip：不调用 AI、不写 failed、
    # 不覆盖 analyzed、不重新入队。`processing` 允许通过，是为了让「进程重启后
    # 被中断的任务」能继续（那时已经没有在飞的请求）。
    #
    # 位置刻意在 set processing **之前**，并且与写入合并在同一条 UPDATE 里，
    # 这样与 handoff 的 CAS 在同一行上只能有一个赢家。
    with get_conn(settings.db_path) as conn:
        claimed = repo.claim_for_analysis(conn, content_id)
    if not claimed:
        logger.info(
            "[%s] content_id=%s 已不在待分析状态（可能已转为外部分析），跳过且不调用模型",
            name, content_id,
        )
        return RESULT_SKIPPED

    for attempt in range(MAX_RETRIES + 1):
        # 每次重试前再次确认未取消：取消后立即停止后续尝试
        if run_id is not None and _run_is_cancelled(run_id):
            _revert_to_unprocessed(content_id)
            logger.info("[%s] 运行 %s 在重试前被取消，content_id=%s", name, run_id, content_id)
            return RESULT_SKIPPED

        try:
            await analyze_content(content_id)
            logger.info("[%s] 分析完成 content_id=%s", name, content_id)
            return RESULT_OK
        except asyncio.CancelledError:
            raise
        except AIError as err:
            if not err.retryable or attempt >= MAX_RETRIES:
                _mark_failed(content_id, str(err))
                logger.warning(
                    "[%s] 分析失败（%s）content_id=%s: %s",
                    name,
                    "不可恢复" if not err.retryable else "重试已用尽",
                    content_id,
                    err,
                )
                return RESULT_FAILED
            delay = min(BASE_BACKOFF * (2**attempt), MAX_BACKOFF)
            logger.info(
                "[%s] 可恢复错误，%.1fs 后重试（%s/%s）content_id=%s: %s",
                name, delay, attempt + 1, MAX_RETRIES, content_id, err,
            )
            await asyncio.sleep(delay)
        except Exception as err:  # 未知异常：不重试，明确失败
            _mark_failed(content_id, f"{type(err).__name__}: {err}")
            logger.exception("[%s] 分析异常 content_id=%s", name, content_id)
            return RESULT_FAILED

    return RESULT_FAILED


async def _worker(name: str) -> None:
    assert _queue is not None
    while True:
        content_id, run_id = await _queue.get()
        result = RESULT_FAILED
        try:
            result = await _run_one(content_id, name, run_id)
        except asyncio.CancelledError:
            raise
        finally:
            _inflight.discard(content_id)
            _queue.task_done()
        if run_id is not None:
            _bump_run_progress(run_id, result)
        if result == RESULT_OK:
            notify_analysis_completed()


# ---------------------------------------------------------------- 风格档案自动更新

def notify_analysis_completed() -> None:
    """分析完成通知 —— 实时 worker 与 Batch **共用**的唯一风格更新入口。

    不复制任何风格更新算法：内部仍走 _schedule_style_update()，
    是否真正重算由 profile_staleness() 判断（例如「新增满 5 条」）。
    因此重复调用是安全的，未达阈值时不会有额外开销。

    Batch 只在一个批次的结果全部写回后调用一次，不必每条都触发。
    """
    _schedule_style_update()


def notify_style_evidence_changed() -> None:
    """风格证据集合发生变化（资格开关 / source 变更）后的通知入口。

    **这不是第二套任务系统**：它仍然复用 _schedule_style_update() →
    _style_update_task() 这条现有链路，以及既有的单实例并发控制。

    它解决的是一个纯粹的失效问题：
    用户关闭某条内容的风格资格后，eligible 数量会**变少**，
    `new_since = eligible - based_on` 可能为负数，
    单靠「累计满 5 条」的阈值，旧档案会永远保留已被排除的样本。

    本入口只是「问一声」，真正是否重算仍由 profile_staleness() 决定 ——
    它额外检查当前档案的 sample_ids 中是否已有失效样本，
    有则立即重算，没有则不受影响（新增样本仍走原累计策略）。
    """
    _schedule_style_update()


def _schedule_style_update() -> None:
    """在后台调度一次风格档案更新（绝不阻塞分析 worker）。

    若已有更新任务在跑就不重复调度：它会在结束后自行判断是否需要续跑，
    因此不会因为锁被占用而漏掉批量分析期间新增的内容。
    """
    global _style_task
    if _style_lock is None:
        return  # 服务未启动（或已停止）：不创建无效任务
    if _style_task is not None and not _style_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _style_task = loop.create_task(_style_update_task())
    _style_task.add_done_callback(_on_style_task_done)


def _on_style_task_done(task: asyncio.Task) -> None:
    """风格档案任务完成回调：需要时自动续跑下一轮。

    两个关键约束：
    1. 只在当前任务**已经 done 之后**才创建下一个 task —— 因此不可能出现
       并发的第二个 style task；
    2. 续跑由本回调发起，不依赖「下一篇内容分析完成」这类外部事件 ——
       即使批量分析已全部结束，也能自己把档案补到追上数据。

    create_task 只是排入事件循环，实际执行要等下一轮，因此不会形成忙循环。
    """
    global _style_task, _style_continue

    if _style_task is task:
        _style_task = None

    if not _style_continue:
        return
    _style_continue = False

    if _style_lock is None:      # 服务已停止，不再续跑
        return

    _schedule_style_update()


async def _style_update_task() -> None:
    """STYLE 增量学习（架构 v3 / A 方案）。

    取代旧的"每新增 5 篇就重算一次、最多送 20 篇原文"的主路径：

      - 有效已分析本人作品 **< 20** 且从未建档 → **不启动** STYLE；
      - 达到 **20** 且从未建档            → 首次集中建档（学完这 20 篇）；
      - 已建档                            → 只学**新增篇**（逐篇增量）。

    旧的 MAX_STYLE_CATCHUP 追赶循环不再需要：增量学习一次就能学完所有
    待学作品，不存在"越追越落后"的情况。
    """
    global _style_continue

    from app.services.style_learning import sync_style_learning

    assert _style_lock is not None
    _style_continue = False

    async with _style_lock:
        try:
            with get_conn(settings.db_path) as conn:
                result = await sync_style_learning(conn)

            action = result.get("action")
            if action == "waiting":
                logger.info("STYLE 学习等待中：%s", result.get("reason"))
                return
            if action == "noop":
                return

            logger.info(
                "STYLE %s：学会 %s 篇（有效 %s 篇），档案 v%s",
                "首次建档" if action == "initial" else "增量学习",
                result.get("learned"),
                result.get("eligible_count"),
                result.get("profile_version"),
            )
        except Exception:  # noqa: BLE001
            logger.exception("STYLE 增量学习失败（不影响内容分析）")
    logger.info(
        "风格档案已达单任务上限（%s 轮）且仍有增量，任务结束后将自动续跑",
        MAX_STYLE_CATCHUP,
    )


# ---------------------------------------------------------------- 取消分析运行

async def finalize_realtime_cancel(run_id: int) -> int:
    """把某次实时运行的剩余内容退回「未分析」，并把运行标记为 cancelled。

    已成功分析的结果保持不变 —— 用户取消只影响**尚未执行**的部分。
    返回退回的内容条数。
    """
    with get_conn(settings.db_path) as conn:
        run = repo.get_analysis_run(conn, run_id)
        if run is None:
            return 0
        batch_id = run["import_batch_id"]
        remaining = (
            repo.list_content_ids_for_run(
                conn, batch_id, ("unprocessed", "queued", "processing")
            )
            if batch_id
            else []
        )
        for cid in remaining:
            repo.set_analysis_status(conn, cid, "unprocessed")
        repo.update_analysis_run(conn, run_id, status="cancelled")

    for cid in remaining:
        # 允许这些内容以后被重新入队（不算重复）
        _inflight.discard(cid)
    return len(remaining)


async def cancel_realtime_run(run_id: int) -> dict:
    """请求取消一次实时分析运行。

    1. 先持久化 cancelling —— worker 会在取到任务时发现并跳过，
       因此不会再发起新的模型调用；
    2. 随后收尾：剩余内容回到 unprocessed，运行标记 cancelled；
    3. 已经在途的少数请求无法保证远端立即停止（不影响本地状态收敛）。
    """
    from app.services.errors import NotFoundError

    with get_conn(settings.db_path) as conn:
        run = repo.get_analysis_run(conn, run_id)
        if run is None:
            raise NotFoundError(f"分析运行 {run_id} 不存在")
        if run["status"] in ("completed", "failed", "cancelled"):
            return {
                "run_id": run_id,
                "status": run["status"],
                "reverted": 0,
                "already_finished": True,
            }
        repo.update_analysis_run(conn, run_id, status="cancelling")

    reverted = await finalize_realtime_cancel(run_id)
    logger.info("实时分析运行 %s 已取消，退回未分析 %s 条", run_id, reverted)
    return {"run_id": run_id, "status": "cancelled", "reverted": reverted}


# ---------------------------------------------------------------- 生命周期

async def start_workers() -> None:
    """启动 worker，并恢复**用户明确要求过分析**的未完成任务。"""
    global _queue, _style_lock, _style_task, _style_continue

    _queue = asyncio.Queue()
    _style_lock = asyncio.Lock()
    _style_task = None
    _style_continue = False

    for i in range(WORKER_COUNT):
        _workers.append(
            asyncio.create_task(_worker(f"worker-{i}"), name=f"analysis-{i}")
        )

    # 1) 恢复被中断的分析。
    #    只认 queued / processing —— 用户点过「分析」但没跑完的。
    #    unprocessed（例如刚导入或已被取消）绝不被自动恢复。
    with get_conn(settings.db_path) as conn:
        resumable = repo.list_content_ids_by_analysis_state(
            conn, ("queued", "processing")
        )
    recovered = sum(1 for cid in resumable if enqueue_analysis(cid))

    # 2) 收尾上次未完成取消的实时运行
    with get_conn(settings.db_path) as conn:
        pending_cancel = [
            r for r in repo.list_unfinished_runs(conn)
            if r["kind"] == "realtime" and r["status"] == "cancelling"
        ]
    for run in pending_cancel:
        await finalize_realtime_cancel(int(run["id"]))

    # 3) 把队列创建前暂存的任务补入
    staged = list(_pending)
    _pending.clear()
    for item in staged:
        _queue.put_nowait(item)

    logger.info(
        "分析队列已启动：worker=%s，恢复分析中 %s 条，收尾取消 %s 个，补入暂存 %s 条",
        WORKER_COUNT, recovered, len(pending_cancel), len(staged),
    )


async def stop_workers() -> None:
    global _style_lock, _style_task, _style_continue
    for task in _workers:
        task.cancel()
    if _style_task is not None and not _style_task.done():
        _style_task.cancel()

    for task in [*_workers, _style_task]:
        if task is None:
            continue
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("停止任务时忽略异常", exc_info=True)

    _workers.clear()
    _style_task = None
    _style_lock = None
    _style_continue = False


def queue_size() -> int:
    """待处理任务数（含队列内与暂存区）。"""
    return (_queue.qsize() if _queue else 0) + len(_pending)
