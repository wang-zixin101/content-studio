@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0backend\.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
echo ERROR: Python virtual environment not found.
echo Expected: backend\.venv\Scripts\python.exe
pause
exit /b 1
)

where npm >nul 2>&1
if errorlevel 1 (
echo ERROR: npm not found.
pause
exit /b 1
)

if not exist "frontend\package.json" (
echo ERROR: frontend\package.json not found.
pause
exit /b 1
)

echo Starting backend...
start "Content Studio Backend" cmd /k ""%PYTHON%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend"
%SystemRoot%\System32\timeout.exe /t 2 /nobreak >nul

echo Starting frontend...
start "Content Studio Frontend" cmd /k "cd /d "%~dp0frontend" && npm run dev"
%SystemRoot%\System32\timeout.exe /t 3 /nobreak >nul

start "" "http://localhost:5173"

echo Content Studio started.
exit /b 0
