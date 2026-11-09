@echo off
REM Copyright (C) Dittobuffer Contributors
REM Copyright (C) chucny
REM Copyright (C) gekkedavi
REM
REM This file is part of Dittobuffer. It is distributed under the GNU General
REM Public License v3.0 (see LICENSE). The copyright notices in this file must
REM be preserved in all copies, forks, and derivative works. Removing or
REM altering these notices violates the license and copyright law.

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
