@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Dittobuffer Platform Reset
cd /d "%~dp0"
set "ROOT=%CD%"
set "PY=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo This backs up and removes only platform config/state JSON.
echo Server profiles, player databases and game data are not touched.
echo.
"%PY%" "%ROOT%\platform\run.py" --reset --server-root "%ROOT%\server"
echo.
pause
