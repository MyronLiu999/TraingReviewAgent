#!/usr/bin/env bash
# Mac / Linux: first run sets everything up, later runs just start the bot.
cd "$(dirname "$0")"
PY=python3; command -v python3 >/dev/null || PY=python
if [ ! -d .venv ]; then
  echo "Setting up (first run only)..."
  $PY -m venv .venv || { echo "Python 3.11+ is required: https://www.python.org/downloads/"; exit 1; }
  .venv/bin/pip install -q -r requirements.txt || exit 1
fi
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env - open it, fill in OPENAI_API_KEY and TELEGRAM_BOT_TOKEN (README step 4), then run ./start.sh again."
  exit 1
fi
if ! grep -qE '^TELEGRAM_CHAT_ID=-?[0-9]+' .env; then
  echo "Finding your Telegram chat ID..."
  .venv/bin/python get_chat_id.py || exit 1
fi
echo "Testing OpenAI connection..."
.venv/bin/python main.py --check "Test: Fed holds rates steady as expected" || exit 1
echo "Starting bot (press Ctrl+C to stop)..."
.venv/bin/python main.py
