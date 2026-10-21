"""日志初始化。

开发模式：保持原来的控制台输出（等价于此前 main.py 里的 logging.basicConfig）。
冻结模式：桌面版以 --noconsole 启动，stderr 不可见，必须同时落文件，
          否则启动异常会凭空消失。文件位于 %LOCALAPPDATA%\\ContentStudio\\logs\\app.log。

刻意不做：telemetry、crash reporter、日志上传、UI 日志页。
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import LOG_DIR, is_frozen

LOG_FILE_NAME = "app.log"
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

_configured = False


def log_file_path() -> Path:
    """冻结态日志文件路径（开发模式也返回路径，但不写入）。"""
    return LOG_DIR / LOG_FILE_NAME


def setup_logging() -> Path | None:
    """配置根 logger。

    返回实际写入的日志文件路径；开发模式不写文件，返回 None。

    幂等：launcher 与 app.main 都会调用，重复调用只生效一次，
    避免两边互相覆盖 handler。
    """
    global _configured

    if _configured:
        return log_file_path() if is_frozen() else None

    formatter = logging.Formatter(LOG_FORMAT)
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # --noconsole 下 sys.stderr 为 None，此时不要挂 stream handler
    if sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(formatter)
        root.addHandler(console)

    file_path: Path | None = None
    if is_frozen():
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_path = log_file_path()
        file_handler = RotatingFileHandler(
            file_path,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    _configured = True
    return file_path
