@echo off
setlocal enabledelayedexpansion

echo ===================================================
echo     Literature Lab - Automated Setup for Windows
echo ===================================================
echo.

:: 1. Check Python installation
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not found in your PATH!
    echo Please install Python 3.10, 3.11, or 3.12 from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version 2^>^&1') do set PYTHON_VER=%%i
echo [*] Detected %PYTHON_VER%

:: 2. Create virtual environment if missing
if not exist ".venv" (
    echo [*] Creating virtual environment (.venv)...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment!
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created.
) else (
    echo [*] Virtual environment (.venv) already exists.
)

:: 3. Upgrade pip and install dependencies
echo [*] Upgrading pip and installing required packages...
call .\.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Package installation failed!
    pause
    exit /b 1
)
echo [OK] All dependencies successfully installed.

:: 4. Copy .env.example if .env does not exist
if not exist ".env" (
    if exist ".env.example" (
        echo [*] Initializing .env configuration from .env.example...
        copy .env.example .env >nul
        echo [OK] Created .env file.
    )
)

echo.
echo ===================================================
echo   Setup Completed Successfully!
echo ===================================================
echo.
echo To start Literature Lab:
echo   1. Double-click run.bat (or run: .\.venv\Scripts\python main.py)
echo   2. Open http://localhost:8001 in your browser
echo   3. Visit http://localhost:8001/settings to set your free Gemini API key
echo.
pause
