#!/usr/bin/env bash
# Render start command:  ./render_start.sh
# One Web Service runs both: the background agent worker (emails, invoices,
# order jobs) and the Gunicorn web server.
set -o errexit
python manage.py run_agents --watch --interval 15 &
exec gunicorn boutique.wsgi:application \
    --bind 0.0.0.0:${PORT:-10000} \
    --workers ${WEB_CONCURRENCY:-2} \
    --timeout 120 \
    --access-logfile -
