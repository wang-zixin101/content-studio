"""按配置创建 AI Provider。

业务代码通过 get_provider() 获取，永远不直接 import 具体实现。
新增厂商：写一个 AIProvider 子类，在 _REGISTRY 注册即可。
"""

from __future__ import annotations

from app.config import Settings, settings as default_settings

from .base import AIProvider
from .openai_compat import OpenAICompatProvider

# provider 注册表：name -> 工厂函数
_REGISTRY: dict[str, type] = {
    "openai_compat": OpenAICompatProvider,
}

_cached: AIProvider | None = None
_cached_key: tuple | None = None
_batch_cached: AIProvider | None = None
_batch_cached_key: tuple | None = None


def _build(cfg: Settings) -> AIProvider:
    cls = _REGISTRY.get(cfg.ai_provider)
    if cls is None:
        raise ValueError(
            f"未知的 AI_PROVIDER='{cfg.ai_provider}'，可选：{list(_REGISTRY)}"
        )

    if cls is OpenAICompatProvider:
        return cls(cfg.ai_base_url, cfg.ai_api_key, cfg.ai_timeout)

    return cls()  # type: ignore[call-arg]


def get_provider(cfg: Settings | None = None) -> AIProvider:
    """返回当前的 AI Provider（带缓存，配置变化时自动重建）。"""
    global _cached, _cached_key
    cfg = cfg or default_settings

    key = (cfg.ai_provider, cfg.ai_base_url, cfg.ai_api_key, cfg.ai_timeout)
    if _cached is None or _cached_key != key:
        _cached = _build(cfg)
        _cached_key = key
    return _cached


def get_batch_provider(cfg: Settings | None = None) -> AIProvider | None:
    """返回 Batch 专用 Provider。

    Batch 使用**独立**的 Base URL（MiMo 的 Batch 端点是独立域名）。
    未显式配置 AI_BATCH_BASE_URL 时返回 None —— 调用方应据此判定 Batch 不可用，
    而不是退回到实时端点。
    """
    global _batch_cached, _batch_cached_key
    cfg = cfg or default_settings

    if not cfg.batch_configured:
        return None

    key = (cfg.ai_batch_base_url, cfg.batch_key, cfg.ai_timeout)
    if _batch_cached is None or _batch_cached_key != key:
        _batch_cached = OpenAICompatProvider(
            cfg.ai_batch_base_url, cfg.batch_key, cfg.ai_timeout
        )
        _batch_cached_key = key
    return _batch_cached


def reset_provider() -> None:
    """清除缓存（配置热更新或测试时使用）。"""
    global _cached, _cached_key, _batch_cached, _batch_cached_key
    _cached = None
    _cached_key = None
    _batch_cached = None
    _batch_cached_key = None
