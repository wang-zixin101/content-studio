"""Personalization 状态接口。"""

from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from app.db import get_conn
from app.services import personalization

router = APIRouter(prefix="/api/personalization", tags=["personalization"])


@router.get("")
def state() -> dict:
    """当前处于 COLD_START / LEARNING / PERSONALIZED，以及判定依据。"""
    with get_conn(settings.db_path) as conn:
        return personalization.resolve_state(conn)
