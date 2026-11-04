@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Install Python 3.12 or newer with its Python launcher first. See docs/setup.md.
  pause
  exit /b 1
)
py -3 rove.py setup %*
pause
