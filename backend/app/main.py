"""FastAPI 应用入口。

开发时前端由 Vite 提供；若存在 frontend/dist，则同时提供静态托管（单端口运行）。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    analysis,
    authors,
    contents,
    external_analysis,
    imports,
    insights,
    materials,
    mine,
    personalization,
    rewrites,
    style,
    system,
)
from app.config import ENV_FILE, RESOURCE_ROOT, settings
from app.db import init_db
from app.jobs import start_workers, stop_workers
from app.logging_setup import setup_logging
from app.services import batch as batch_service
from app.services.errors import ServiceError

setup_logging()
logger = logging.getLogger("content_studio")

# 只读资源根：dev = content-studio/ ；frozen = <exe>/_internal
DIST_DIR = RESOURCE_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(settings.db_path)
    logger.info("数据库就绪：%s", settings.db_path)
    if not settings.ai_configured:
        logger.warning("AI_API_KEY 未配置：分析/改写将失败，请在 %s 中填入密钥", ENV_FILE)
    await start_workers()
    # Batch 常驻监控：先恢复未完成任务，之后持续轮询直到写回（只查询不提交）
    batch_monitor = asyncio.create_task(batch_service.monitor_loop())
    yield
    batch_monitor.cancel()
    try:
        await batch_monitor
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    await stop_workers()


app = FastAPI(title="X 内容工作台", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


for router in (
    system.router,
    contents.router,
    imports.router,
    analysis.router,
    authors.router,
    external_analysis.router,
    insights.router,
    materials.router,
    mine.router,
    rewrites.router,
    style.router,
    personalization.router,
):
    app.include_router(router)


# ---- 可选：静态托管前端构建产物（SPA fallback）----
if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        candidate = DIST_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DIST_DIR / "index.html")
