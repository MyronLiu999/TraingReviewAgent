#!/usr/bin/env bash
# Mac / Linux: first run sets everything up, later runs just start the app.
cd "$(dirname "$0")"
PY=python3; command -v python3 >/dev/null || PY=python
if [ ! -d .venv ]; then
  echo "Setting up (first run only)..."
  $PY -m venv .venv || { echo "Python 3.11+ is required: https://www.python.org/downloads/"; exit 1; }
  .venv/bin/pip install -q -r requirements.txt || exit 1
fi
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env - open it, set APP_PASSWORD and OPENAI_API_KEY, then run ./start.sh again."
  exit 1
fi
if grep -qE '^TELEGRAM_BOT_TOKEN=[0-9]+:' .env && ! grep -qE '^TELEGRAM_CHAT_ID=-?[0-9]+' .env; then
  echo "Finding your Telegram chat ID..."
  .venv/bin/python get_chat_id.py || echo "(Telegram skipped for now - the web UI still works)"
fi
.venv/bin/python web.py
