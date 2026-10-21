"""Content Studio Desktop V1 —— 桌面启动器。

职责边界（不要扩大）：
  起一个内部 uvicorn（127.0.0.1:8765，同时服务 /api/* 与打包进去的 React dist），
  等 /api/health 就绪后用 pywebview 打开原生窗口；窗口关闭后让 uvicorn 干净退出。

刻意不做：多窗口、frameless、自定义 titlebar、tray、splash、always-on-top、
系统菜单扩展、single-instance framework、IPC、自动更新、installer、pywebview JS API。
也不启动任何 Python subprocess。

线程模型（不可颠倒）：
    pywebview 的 GUI 循环必须在**主线程**；uvicorn 放后台守护线程。
    反过来会直接失败。
"""

from __future__ import annotations

import ctypes
import http.client
import logging
import socket
import sys
import threading
import time
from pathlib import Path

# 源码方式运行（python desktop/main.py）时把 backend/ 加进 sys.path。
# 冻结后 backend/ 已打成顶层 `app` 包，这一步跳过。
if not getattr(sys, "frozen", False):
    _BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
    if str(_BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(_BACKEND_DIR))

from app.config import (  # noqa: E402
    APP_DATA_ROOT,
    DATA_DIR,
    LOG_DIR,
    RESOURCE_ROOT,
    is_frozen,
)
from app.logging_setup import setup_logging  # noqa: E402

HOST = "127.0.0.1"
PORT = 8765
WINDOW_TITLE = "Content Studio"
WINDOW_WIDTH = 1440
WINDOW_HEIGHT = 900
WINDOW_MIN_SIZE = (1100, 700)
APP_URL = f"http://{HOST}:{PORT}/"

HEALTH_PATH = "/api/health"
HEALTH_TIMEOUT_SECONDS = 60.0
HEALTH_POLL_INTERVAL = 0.25
SHUTDOWN_JOIN_TIMEOUT = 15.0

MESSAGEBOX_ERROR_ICON = 0x10

WEBVIEW2_HINT = (
    "无法启动 WebView2 渲染器。\n\n"
    "请先安装 Microsoft Edge WebView2 Runtime 后重试：\n"
    "https://developer.microsoft.com/microsoft-edge/webview2/\n"
)

logger = logging.getLogger("content_studio.desktop")


def _message_box(text: str, title: str = WINDOW_TITLE) -> None:
    """Windows 原生 MessageBox —— 没有窗口可用时的最后手段。"""
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, MESSAGEBOX_ERROR_ICON)
    except Exception:  # noqa: BLE001
        logger.exception("无法弹出原生提示框")


def _port_in_use(host: str, port: int) -> bool:
    """8765 是否已被占用。

    先尝试连接（能连上 = 确实有服务在听），再尝试独占绑定。
    两者都通过才算空闲 —— 只看 bind 在 Windows 上不可靠
    （别的进程绑 0.0.0.0 时，绑 127.0.0.1 仍可能成功）。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.6)
        if probe.connect_ex((host, port)) == 0:
            return True

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as binder:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            binder.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        try:
            binder.bind((host, port))
        except OSError:
            return True
    return False


def _health_ok() -> bool:
    """探活。

    用 http.client 直连而不是 urllib/httpx —— 本机可能配了 HTTP_PROXY，
    urllib 的代理绕过依赖系统注册表，不可靠。直连没有这个问题。
    """
    conn = http.client.HTTPConnection(HOST, PORT, timeout=1.0)
    try:
        conn.request("GET", HEALTH_PATH)
        response = conn.getresponse()
        response.read()
        return response.status == 200
    except Exception:  # noqa: BLE001
        return False
    finally:
        conn.close()


def _wait_for_health() -> bool:
    deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _health_ok():
            return True
        time.sleep(HEALTH_POLL_INTERVAL)
    return False


def _prepare_dirs() -> None:
    """可写目录必须先建好：日志与 SQLite 都落在 APP_DATA_ROOT 下。"""
    for path in (APP_DATA_ROOT, DATA_DIR, LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)


def main() -> int:
    _prepare_dirs()
    log_path = setup_logging()

    logger.info("=" * 60)
    logger.info("Content Studio Desktop 启动")
    logger.info("frozen        = %s", is_frozen())
    logger.info("RESOURCE_ROOT = %s", RESOURCE_ROOT)
    logger.info("APP_DATA_ROOT = %s", APP_DATA_ROOT)
    if log_path is not None:
        logger.info("日志文件      = %s", log_path)

    # --- 端口检查：被占用就明确提示并退出，绝不另起第二个后端 ---
    if _port_in_use(HOST, PORT):
        message = f"Content Studio 已在运行，或端口 {PORT} 被其它程序占用。"
        logger.error(message)
        _message_box(message)
        logging.shutdown()
        return 1

    import uvicorn
    import webview

    # 必须在 webview.start() 之前设置，否则文件下载会被静默拦掉
    webview.settings["ALLOW_DOWNLOADS"] = True

    # 此时 config 已按运行根解析完毕，DIST_DIR 指向 RESOURCE_ROOT/frontend/dist
    from app.main import app as fastapi_app

    config = uvicorn.Config(
        fastapi_app,
        host=HOST,
        port=PORT,
        # 最小协议集：本项目没有 WebSocket
        loop="asyncio",
        http="h11",
        ws="none",
        lifespan="on",
        access_log=False,
        # 日志交给 setup_logging；同时避开 --noconsole 下 stderr 为 None 的问题
        log_config=None,
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, name="content-studio-uvicorn", daemon=True)
    thread.start()
    logger.info("uvicorn 已在后台线程启动：http://%s:%s", HOST, PORT)

    exit_code = 0
    try:
        if not _wait_for_health():
            logger.error("健康检查超时（%.0f 秒），放弃打开窗口", HEALTH_TIMEOUT_SECONDS)
            _message_box(
                "后端服务启动超时，无法打开窗口。\n\n"
                f"请查看日志：{log_path or '（未写文件）'}"
            )
            return 1

        logger.info("健康检查通过，创建窗口")
        webview.create_window(
            WINDOW_TITLE,
            APP_URL,
            width=WINDOW_WIDTH,
            height=WINDOW_HEIGHT,
            min_size=WINDOW_MIN_SIZE,
        )

        # GUI 循环必须在主线程；显式指定 edgechromium，不允许回落到 MSHTML
        webview.start(gui="edgechromium", debug=False)
        logger.info("窗口已关闭")
        return exit_code
    except Exception as exc:  # noqa: BLE001
        logger.exception("窗口启动失败")
        _message_box(f"{WEBVIEW2_HINT}\n错误：{exc}")
        return 1
    finally:
        # 无论窗口是否正常打开，都必须让 uvicorn 退出，不留残留进程
        server.should_exit = True
        thread.join(timeout=SHUTDOWN_JOIN_TIMEOUT)
        if thread.is_alive():
            logger.error("uvicorn 线程在 %.0f 秒内未退出", SHUTDOWN_JOIN_TIMEOUT)
        else:
            logger.info("uvicorn 已退出，launcher 结束")
        logging.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
