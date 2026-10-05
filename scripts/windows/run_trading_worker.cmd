@echo off
rem Keeps the trading worker running on this PC until the paper-trading server
rem exists (memory: project-paper-trading-server, 2026-10-05). Registered as a
rem logon task by scripts/windows/register_trading_worker_task.ps1.
rem Restarts the worker 30 seconds after it exits, so a crash or a database
rem restart does not stop paper trading for good. Output goes to
rem logs\trading-worker-autostart.log.
rem
rem Do not also run the trading worker from scripts/start_local.py while this
rem is running: two workers would evaluate the same bars at the same time.
title AI System Trading - trading worker (autostart)
cd /d "%~dp0\..\.."
if not exist logs mkdir logs
:loop
echo [%date% %time%] starting trading worker>> logs\trading-worker-autostart.log
python -m app.trading.worker >> logs\trading-worker-autostart.log 2>&1
echo [%date% %time%] trading worker exited with %errorlevel%; restarting in 30s>> logs\trading-worker-autostart.log
timeout /t 30 /nobreak >nul
goto loop
