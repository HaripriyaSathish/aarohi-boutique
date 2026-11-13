@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo Install Python 3.10 or newer and select Add Python to PATH.
    pause
    exit /b 1
)
if not exist .venv (
    python -m venv .venv
)
call .venv\Scripts\activate.bat
if not exist .env copy .env.example .env
python -m pip install -r requirements.txt
if errorlevel 1 goto :failed
python manage.py migrate
if errorlevel 1 goto :failed
python -c "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','boutique.settings'); import django; django.setup(); from django.contrib.auth import get_user_model; import sys; sys.exit(0 if get_user_model().objects.filter(is_superuser=True).exists() else 1)"
if errorlevel 1 (
    echo Create your own admin username and password now.
    python manage.py createsuperuser
    if errorlevel 1 goto :failed
    type nul > .admin_setup_done
)
echo Website: http://127.0.0.1:8001/
echo Admin: http://127.0.0.1:8001/admin/
start "Aarohi Automation Worker" cmd /k ""%~dp0RUN_AGENTS_WINDOWS.bat""
python manage.py runserver 8001
pause
exit /b 0
:failed
echo Setup stopped. Read the error above and try again.
pause
exit /b 1
