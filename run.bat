@echo off
setlocal

echo ===================================================
echo     Launching Literature Lab...
echo ===================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found!
    echo Please run setup.bat first to configure your environment.
    pause
    exit /b 1
)

echo [*] Starting Literature Lab web server at http://127.0.0.1:8001 ...
echo [*] Press Ctrl+C at any time in this window to stop the server.
echo.

.\.venv\Scripts\python.exe main.py

pause
