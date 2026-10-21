"""AI 能力层：与具体厂商解耦。

上层服务只依赖 AIProvider 抽象；换模型/换厂商只需改 .env 或新增一个实现。
"""

from .base import AIProvider, AIError, extract_json
from .factory import get_provider, reset_provider

__all__ = ["AIProvider", "AIError", "extract_json", "get_provider", "reset_provider"]
