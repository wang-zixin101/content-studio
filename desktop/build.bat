@echo off
REM ============================================================
REM  Content Studio Desktop V1 - build script
REM
REM  Does exactly three things:
REM    1. build the frontend dist   (npm run build)
REM    2. package with PyInstaller  (desktop\ContentStudio.spec)
REM    3. emit release\ContentStudio\ContentStudio.exe
REM
REM  Deliberately NOT included: installer / Setup.exe / registry
REM  entries / auto-updater. Create the desktop shortcut yourself
REM  after a successful build.
REM
REM  IMPORTANT: keep this file ASCII-only.
REM  cmd.exe parses .bat files using the OEM code page, so UTF-8
REM  multi-byte comments get mangled and break command parsing.
REM  Chinese documentation lives in ContentStudio.spec instead.
REM ============================================================
setlocal
cd /d "%~dp0.."

set "PY=backend\.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [ERROR] Python venv not found: %PY%
    exit /b 1
)

echo [1/2] Building frontend dist ...
pushd frontend
call npm run build
set "NPM_ERR=%ERRORLEVEL%"
popd
if not "%NPM_ERR%"=="0" (
    echo [ERROR] frontend build failed.
    exit /b 1
)

if not exist "frontend\dist\index.html" (
    echo [ERROR] frontend\dist\index.html is missing.
    exit /b 1
)

echo [2/2] Packaging with PyInstaller (onedir) ...
"%PY%" -m PyInstaller --noconfirm --clean --distpath release --workpath build\pyinstaller desktop\ContentStudio.spec
if errorlevel 1 (
    echo [ERROR] PyInstaller packaging failed.
    exit /b 1
)

echo.
echo Done: release\ContentStudio\ContentStudio.exe
endlocal
