@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First, double-click Setup Rove.cmd in this folder.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" rove_ui.py %*
if errorlevel 1 (
  echo Rove could not open. See the message above.
  pause
  exit /b 1
)
