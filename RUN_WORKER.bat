@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" manage.py run_agents --watch --interval 5
pause
