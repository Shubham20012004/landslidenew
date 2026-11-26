#!/usr/bin/env bash
# ./train.sh            train on labelled real experimental data (activates if valid)
# ./train.sh synthetic  regenerate and train the SYNTHETIC demonstration model
set -euo pipefail
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "Run ./setup.sh first."; exit 1; }
if [ "${1:-}" = "synthetic" ]; then
  .venv/bin/python -m ml.synthetic
  exec .venv/bin/python -m ml.train --source synthetic --activate
fi
exec .venv/bin/python -m ml.train --source experimental --activate
