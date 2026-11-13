#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
[ -f .env ] || cp .env.example .env
python -m pip install -r requirements.txt
python manage.py migrate
if ! python -c "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','boutique.settings'); import django; django.setup(); from django.contrib.auth import get_user_model; import sys; sys.exit(0 if get_user_model().objects.filter(is_superuser=True).exists() else 1)"; then
    python manage.py createsuperuser
    touch .admin_setup_done
fi
python manage.py run_agents --watch &
task_worker_pid=$!
trap 'kill "$task_worker_pid" 2>/dev/null || true' EXIT
python manage.py runserver 8001
