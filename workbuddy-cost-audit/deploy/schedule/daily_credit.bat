@echo off
rem CostAudit daily credit reader. ASCII only on purpose:
rem cmd.exe parses this file with the OEM code page, non-ASCII breaks line boundaries.
setlocal
cd /d "%~dp0..\.."
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not exist out mkdir out
rem --log tees stdout/stderr into the file; do NOT also append with >> here,
rem two writers on one log interleave and produce half lines.
"%PY%" -m hosts.commands.daily_credit --out out --format html --quiet --log out\daily_credit.log
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
