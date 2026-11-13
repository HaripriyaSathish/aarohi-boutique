@echo off
cd /d "%~dp0"
echo Website: http://127.0.0.1:8001/
".venv\Scripts\python.exe" manage.py runserver 8001
pause
