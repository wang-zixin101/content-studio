"""系统信息：健康检查与配置状态（不泄露密钥）。"""

from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from app.jobs import queue_size
from app.services import batch as batch_service

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health() -> dict:
    return {"ok": True}


@router.get("/config")
def config() -> dict:
    """前端启动时读取，用于提示是否已配置密钥 / Batch 是否可用。"""
    return {
        "ai_configured": settings.ai_configured,
        "ai_provider": settings.ai_provider,
        "ai_base_url": settings.ai_base_url,
        "models": {
            "analysis": settings.model_analysis,
            "rewrite": settings.model_rewrite,
            "style": settings.model_style,
            "batch": settings.model_batch,
        },
        "queue_size": queue_size(),
        # Batch 需要独立的 Base URL；未配置时前端提示 Batch 不可用
        "batch_available": batch_service.available(),
        "batch_base_url": settings.ai_batch_base_url or None,
    }
