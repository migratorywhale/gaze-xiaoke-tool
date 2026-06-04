#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3.12}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "python3.12 was not found. Install Python 3.12 or run with PYTHON_BIN=/path/to/python."
  exit 1
fi

"$PYTHON_BIN" -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-macos.txt

if [ ! -f .env ]; then
  cp .env.example .env
fi

echo "Installed gaze into $(pwd)/.venv"
echo "Edit .env, then test with:"
echo "  . .venv/bin/activate && python gaze_local.py --once --dry-run --caption-provider none"
