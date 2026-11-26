#!/usr/bin/env bash
# Starts the backend, live stream and dashboard.  ./start.sh  |  ./start.sh demo
set -euo pipefail
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "Run ./setup.sh first."; exit 1; }
if [ "${1:-}" = "demo" ]; then
  export LANDSLIDE_DEMO_MODE=true
  echo "DEMONSTRATION MODE: predictions may come from a model trained on synthetic data."
fi
exec .venv/bin/python -m backend.run
