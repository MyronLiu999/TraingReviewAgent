@echo off
REM Windows: double-click this file. First run sets everything up, later runs just start the app.
cd /d "%~dp0"
if not exist .venv (
  echo Setting up - first run only...
  python -m venv .venv || (echo Python 3.11+ is required: https://www.python.org/downloads/ - tick "Add python.exe to PATH" & pause & exit /b 1)
  .venv\Scripts\pip install -q -r requirements.txt || (pause & exit /b 1)
)
if not exist .env (
  copy .env.example .env >nul
  echo Created .env - set APP_PASSWORD and OPENAI_API_KEY in Notepad, save, then double-click start.bat again.
  notepad .env
  pause & exit /b 1
)
findstr /r /c:"^TELEGRAM_BOT_TOKEN=[0-9]*:" .env >nul && (
  findstr /r /c:"^TELEGRAM_CHAT_ID=[0-9-]" .env >nul || (
    echo Finding your Telegram chat ID...
    .venv\Scripts\python get_chat_id.py || echo Telegram skipped for now - the web UI still works
  )
)
.venv\Scripts\python web.py
pause
