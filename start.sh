#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Project environment is missing. Run: bash setup.sh"
  exit 1
fi

if [[ ! -f ".env" ]]; then
  cp .env.example .env
  echo "Created .env. Add the API keys, then run: bash start.sh"
  exit 1
fi

URL="http://127.0.0.1:8765/"
if command -v open >/dev/null 2>&1; then
  open "$URL" >/dev/null 2>&1 || true
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" >/dev/null 2>&1 || true
fi

echo "JOTO GEO Content Hub: $URL"
exec .venv/bin/python web_app.py
