@echo off
REM Windows: double-click this file. First run sets everything up, later runs just start the bot.
cd /d "%~dp0"
if not exist .venv (
  echo Setting up - first run only...
  python -m venv .venv || (echo Python 3.11+ is required: https://www.python.org/downloads/ - tick "Add python.exe to PATH" & pause & exit /b 1)
  .venv\Scripts\pip install -q -r requirements.txt || (pause & exit /b 1)
)
if not exist .env (
  copy .env.example .env >nul
  echo Created .env - fill in OPENAI_API_KEY and TELEGRAM_BOT_TOKEN in Notepad, save, then double-click start.bat again.
  notepad .env
  pause & exit /b 1
)
findstr /r /c:"^TELEGRAM_CHAT_ID=[0-9-]" .env >nul || (
  echo Finding your Telegram chat ID...
  .venv\Scripts\python get_chat_id.py || (pause & exit /b 1)
)
echo Testing OpenAI connection...
.venv\Scripts\python main.py --check "Test: Fed holds rates steady as expected" || (pause & exit /b 1)
echo Starting bot - close this window to stop...
.venv\Scripts\python main.py
pause
