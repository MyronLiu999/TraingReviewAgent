@echo off
REM Windows installer: copies the app to D:\TradingReviewAgent, creates a desktop shortcut, then starts it.
REM Double-click this file inside the downloaded/unzipped folder. Safe to run again to update:
REM your .env, data\ and .venv\ on D: are kept.
setlocal
set "SRC=%~dp0"
if "%SRC:~-1%"=="\" set "SRC=%SRC:~0,-1%"
set "DEST=D:\TradingReviewAgent"

if not exist D:\ (echo Drive D: not found. & pause & exit /b 1)
where python >nul 2>nul || (echo Python 3.11+ is required: https://www.python.org/downloads/ - tick "Add python.exe to PATH" & pause & exit /b 1)

echo Installing to %DEST% ...
robocopy "%SRC%" "%DEST%" /E /NFL /NDL /NJH /NJS /NP /XD .git .venv data __pycache__ /XF .env >nul
if errorlevel 8 (echo Copy failed. & pause & exit /b 1)
if not exist "%DEST%\.env" if exist "%SRC%\.env" copy "%SRC%\.env" "%DEST%\.env" >nul

echo Creating desktop shortcut ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Trading Review Agent.lnk');" ^
  "$s.TargetPath='%DEST%\start.bat'; $s.WorkingDirectory='%DEST%';" ^
  "$s.IconLocation='%SystemRoot%\System32\shell32.dll,167'; $s.Description='Trading Review Agent (http://localhost:8000)'; $s.Save()"

echo.
echo Done. From now on, double-click "Trading Review Agent" on your desktop.
echo Starting the app now ...
cd /d "%DEST%"
call start.bat
