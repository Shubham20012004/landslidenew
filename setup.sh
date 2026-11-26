#!/usr/bin/env bash
# One-time setup on Linux (Ubuntu): virtual environment, dependencies, configuration, database.
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
command -v "$PY" >/dev/null || { echo "ERROR: python3 not found (sudo apt install python3 python3-venv)"; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || { echo "ERROR: Python 3.10+ required"; exit 1; }
if [ ! -x .venv/bin/python ]; then
  echo "[1/5] Creating virtual environment .venv"
  "$PY" -m venv .venv || { echo "ERROR: install python3-venv (sudo apt install python3-venv)"; exit 1; }
else
  echo "[1/5] Using existing virtual environment .venv"
fi
echo "[2/5] Installing dependencies"
.venv/bin/python -m pip install --disable-pip-version-check -q --upgrade pip
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt
echo "[3/5] Configuration (.env, device and admin tokens)"
.venv/bin/python scripts/configure.py
echo "[4/5] Database migration (a backup is written first if data exists)"
.venv/bin/python -m backend.manage migrate
echo "[5/5] Checking configuration and model"
.venv/bin/python -m backend.manage check || true
echo
echo "Setup complete. Start with: ./start.sh   (or ./start.sh demo for the synthetic demo model)"
