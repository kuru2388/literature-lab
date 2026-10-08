#!/usr/bin/env bash
set -e

echo "==================================================="
echo "    Literature Lab - Automated Setup for Unix/macOS"
echo "==================================================="
echo ""

# 1. Check Python installation
if command -v python3 >/dev/null 2>&1; then
    PY_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PY_CMD="python"
else
    echo "[ERROR] Python 3 was not found on your system."
    echo "Please install Python 3.10+ (e.g. via brew install python or apt install python3 python3-venv)"
    exit 1
fi

echo "[*] Using $($PY_CMD --version)"

# 2. Create virtual environment
if [ ! -d ".venv" ]; then
    echo "[*] Creating virtual environment (.venv)..."
    $PY_CMD -m venv .venv
    echo "[OK] Virtual environment created."
else
    echo "[*] Virtual environment (.venv) already exists."
fi

# 3. Activate and install requirements
echo "[*] Upgrading pip and installing dependencies..."
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
echo "[OK] All dependencies successfully installed."

# 4. Copy .env.example if missing
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    echo "[*] Initializing .env configuration from .env.example..."
    cp .env.example .env
    echo "[OK] Created .env file."
fi

# Make run.sh executable if present
if [ -f "run.sh" ]; then
    chmod +x run.sh
fi

echo ""
echo "==================================================="
echo "  Setup Completed Successfully!"
echo "==================================================="
echo ""
echo "To start Literature Lab:"
echo "  1. Run: ./run.sh   (or: source .venv/bin/activate && python main.py)"
echo "  2. Open http://localhost:8001 in your browser"
echo "  3. Go to http://localhost:8001/settings to set your free Gemini API key"
echo ""
