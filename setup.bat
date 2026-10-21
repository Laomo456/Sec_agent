@echo off
setlocal
cd /d "%~dp0"
set "PY=py"
set "PYTHONIOENCODING=utf-8"
where py >nul 2>nul
if errorlevel 1 set "PY=python"
%PY% -m venv gateway\.venv
if errorlevel 1 goto fail
gateway\.venv\Scripts\python.exe -m pip install -r gateway\requirements.txt -r toolbox\requirements.txt
if errorlevel 1 goto fail
echo.
echo Downloading third-party tools from official releases (nuclei / katana / zap / frida ...)...
echo (CN networks: set AGG_GH_PROXY first, see README)
%PY% install_tools.py
echo.
echo Setup complete. Run start.bat, then configure your model in Settings.
pause
exit /b 0
:fail
echo Setup failed. Install Python 3.12 or newer with the py launcher and retry.
pause
exit /b 1
