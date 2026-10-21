# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec —— Content Studio Desktop V1（onedir，无 console）。

构建入口用 desktop/build.bat，它固定传 --distpath release。
手工构建时也必须显式指定，否则会生成仓库根的 dist/，与 Vite 的 frontend/dist 混淆：

    backend\\.venv\\Scripts\\python.exe -m PyInstaller --noconfirm --clean ^
        --distpath release --workpath build\\pyinstaller desktop\\ContentStudio.spec

产物结构：

    release/ContentStudio/
    +-- ContentStudio.exe
    +-- _internal/
        +-- frontend/dist/     <- React 构建产物（只读资源，对应 RESOURCE_ROOT）
        +-- ...                <- Python 运行时与依赖

明确**不**打包：backend/.env、任何 SQLite、tests、shots、.git、.venv*、工作区 memory。
不做 onefile。
"""

from pathlib import Path

_HERE = Path(SPECPATH).resolve()
PROJECT_ROOT = _HERE.parent if _HERE.name == "desktop" else _HERE
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

if not (FRONTEND_DIST / "index.html").is_file():
    raise SystemExit(
        f"未找到 {FRONTEND_DIST / 'index.html'} —— "
        "请先执行 desktop/build.bat（或在 frontend/ 下运行 npm run build）"
    )

a = Analysis(
    [str(PROJECT_ROOT / "desktop" / "main.py")],
    pathex=[str(PROJECT_ROOT / "backend")],
    binaries=[],
    datas=[(str(FRONTEND_DIST), "frontend/dist")],
    # 不手工收集 uvicorn：
    #   uvicorn 用字符串查表做动态导入（loop/http/lifespan），PyInstaller 官方
    #   hook-uvicorn.py 的 collect_submodules('uvicorn') 已覆盖；
    #   pywebview 自带 hook-webview.py 收集 .NET 互操作库与 js 资源。
    # 只有真实 runtime 报缺模块时，才在这里补**精确**条目。
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 非 Windows 的 pywebview GUI 后端（本机一个都没装，排除以防被误收）
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "cefpython3",
        "gi",
        # 本项目零使用
        "tkinter",
        # uvicorn 的可选加速与备选协议实现：
        # launcher 固定 loop="asyncio" / http="h11" / ws="none"，这些永不导入
        "uvloop",
        "zuvloop",
        "websockets",
        "wsproto",
        "httptools",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ContentStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ContentStudio",
)
