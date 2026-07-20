#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3 was not found. Install Python 3.10 or newer first."
  exit 1
fi

"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "Python 3.10 or newer is required."
  exit 1
}

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Creating .venv..."
  "$PYTHON_BIN" -m venv .venv
fi

echo "Installing Python dependencies..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

echo "Installing Playwright Chromium..."
.venv/bin/python -m playwright install chromium

if [[ ! -f ".env" ]]; then
  cp .env.example .env
  echo "Created .env. Add the Dify and Unsplash keys before generating content."
else
  echo "Existing .env kept unchanged."
fi

mkdir -p data outputs
echo "Setup complete. Add keys to .env, then run: bash start.sh"
