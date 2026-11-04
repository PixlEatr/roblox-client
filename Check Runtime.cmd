@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run Setup Rove.cmd first.
  pause
  exit /b 1
)
if not exist outputs mkdir outputs
echo Checking the runtime offline. No game will be joined.
".venv\Scripts\python.exe" scripts\check_portability.py > outputs\windows-runtime-check.log 2>&1
if errorlevel 1 (
  type outputs\windows-runtime-check.log
  echo Runtime check failed. The report is outputs\windows-runtime-check.log
  pause
  exit /b 1
)
echo Runtime checks passed. Report: outputs\windows-runtime-check.log
pause
