#!/usr/bin/env bash
set -e

if [ ! -f ".venv/bin/python" ]; then
    echo "[ERROR] Virtual environment (.venv) not found!"
    echo "Please run ./setup.sh first to set up your environment."
    exit 1
fi

echo "==================================================="
echo "    Launching Literature Lab..."
echo "==================================================="
echo "[*] Server address: http://127.0.0.1:8001"
echo "[*] Press Ctrl+C at any time to stop."
echo ""

.venv/bin/python main.py
