@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Run START_WINDOWS.bat first to install the project.
    pause
    exit /b 1
)
echo Aarohi automation worker is running. Keep this terminal open.
echo Press Ctrl+C to stop the worker.
.venv\Scripts\python.exe manage.py run_agents --watch
pause
