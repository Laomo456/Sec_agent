@echo off
setlocal
cd /d "%~dp0"
if not exist gateway\.venv\Scripts\python.exe (
  echo Run setup.bat first.
  pause
  exit /b 1
)
set "PYTHONIOENCODING=utf-8"
cd gateway
.venv\Scripts\python.exe server.py
pause
