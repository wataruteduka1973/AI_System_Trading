@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 exit /b 1
title AI System Trading - Local

rem Use a project environment, never a Python installation from PATH.
set "LOCAL_PYTHON="
for %%V in (.venv .venv313) do (
    if not defined LOCAL_PYTHON if exist "%%V\Scripts\python.exe" (
        "%%V\Scripts\python.exe" -c "import sys, uvicorn, app.main; assert sys.version_info[:2] == (3, 13)" >nul 2>&1
        if not errorlevel 1 set "LOCAL_PYTHON=%%V\Scripts\python.exe"
    )
)
if not defined LOCAL_PYTHON (
    echo [ERROR] No working project Python 3.13 environment found.
    echo Create .venv or .venv313 and install the project dependencies.
    echo See README.md. No packages or settings were changed.
    pause
    exit /b 1
)
"%LOCAL_PYTHON%" scripts\start_local.py %*
set "LOCAL_EXIT=%ERRORLEVEL%"
if not "%LOCAL_EXIT%"=="0" pause
exit /b %LOCAL_EXIT%
