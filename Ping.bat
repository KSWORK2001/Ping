@echo off
REM ============================================================
REM  Ping launcher - portable, runs on any Windows device.
REM  Keep this file IN the Ping project folder. On the Desktop,
REM  use the shortcut that setup.ps1 creates (it points here).
REM  On first run it installs dependencies and creates .env.
REM  DPI/coordinate awareness is set by the bot itself at start,
REM  so it is configured automatically on every device.
REM ============================================================
title Ping bot
cd /d "%~dp0"

if not exist "bot.py" (
  echo [Ping] bot.py is not next to this launcher.
  echo        Keep Ping.bat inside the project folder, or run setup.ps1
  echo        to create a proper Desktop shortcut.
  pause
  exit /b 1
)

REM --- pick an available Python (python, then the py launcher) ---
set "PY=python"
where python >nul 2>&1 || set "PY=py"
%PY% --version >nul 2>&1 || (
  echo [Ping] Python was not found on this device. Install Python 3.10+ first.
  pause
  exit /b 1
)

REM --- first-run: install dependencies only if a key import is missing ---
%PY% -c "import discord, mss, PIL, pyautogui, psutil, aiohttp, pywinauto, dotenv" 1>nul 2>nul
if errorlevel 1 (
  echo [Ping] Installing dependencies (first run on this device)...
  %PY% -m pip install -r requirements.txt
)

REM --- ensure a .env exists; on first run, open it to fill in the token ---
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo [Ping] Created .env from the template.
  echo        Add DISCORD_TOKEN and ALLOWED_USER_IDS, save, then re-run Ping.
  start "" notepad ".env"
  pause
  exit /b 0
)

echo ============================================================
echo   Starting Ping...
echo   Dashboard: http://127.0.0.1:8765
echo   (this window shows live logs; close it to stop the bot)
echo ============================================================
echo.

REM Open the dashboard ~9s later (console-independent delay).
start "" cmd /c "ping -n 10 127.0.0.1 >nul & start http://127.0.0.1:8765"

%PY% bot.py

echo.
echo ============================================================
echo   Ping has stopped (exit code %ERRORLEVEL%).
echo   Press any key to close this window.
echo ============================================================
pause >nul
