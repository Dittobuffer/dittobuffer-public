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
title Dittobuffer Platform
cd /d "%~dp0"
set "ROOT=%CD%"
set "PY=%ROOT%\.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo Creating Python environment...
  python -m venv "%ROOT%\.venv"
  if errorlevel 1 goto fail
)

echo Installing/updating server requirements...
"%PY%" -m pip install --disable-pip-version-check -q -r "%ROOT%\server\requirements.txt"
if errorlevel 1 goto fail

set "PYTHONPATH=%ROOT%\server\src;%ROOT%\server;%PYTHONPATH%"
echo.
echo Starting platform at http://127.0.0.1:8780
echo The first run asks for a room address.
echo.
"%PY%" "%ROOT%\platform\run.py" --server-root "%ROOT%\server"
if errorlevel 1 goto fail
exit /b 0

:fail
echo.
echo Platform start failed. Review the message above.
pause
exit /b 1
