@echo off
rem Keeps the trading worker running on this PC until the paper-trading server
rem exists (memory: project-paper-trading-server, 2026-10-05). Registered as a
rem logon task by scripts/windows/register_trading_worker_task.ps1.
rem Restarts the worker 30 seconds after it exits, so a crash or a database
rem restart does not stop paper trading for good. Output goes to
rem logs\trading-worker-autostart.log.
rem
rem Uses the project's Python (.venv, then .venv313), as start-local.bat does, never
rem a Python from PATH: that one has no project dependencies, and the worker would fail
rem and restart forever.
rem
rem Start the launcher with `start-local.bat --no-trading-worker` while this is running. A
rem second trading worker does no harm (an evaluation is idempotent per bar) but doubles the
rem work and the log.
title AI System Trading - trading worker (autostart)
cd /d "%~dp0\..\.."
if not exist logs mkdir logs
set "LOCAL_PYTHON="
for %%V in (.venv .venv313) do (
    if not defined LOCAL_PYTHON if exist "%%V\Scripts\python.exe" set "LOCAL_PYTHON=%%V\Scripts\python.exe"
)
if not defined LOCAL_PYTHON (
    echo [%date% %time%] no project Python found (.venv or .venv313); see README.md>> logs\trading-worker-autostart.log
    exit /b 1
)
:loop
echo [%date% %time%] starting trading worker>> logs\trading-worker-autostart.log
"%LOCAL_PYTHON%" -m app.trading.worker >> logs\trading-worker-autostart.log 2>&1
echo [%date% %time%] trading worker exited with %errorlevel%; restarting in 30s>> logs\trading-worker-autostart.log
timeout /t 30 /nobreak >nul
goto loop
