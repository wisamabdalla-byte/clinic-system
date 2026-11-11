#!/usr/bin/env bash
# Runs on the hosting server at every deploy.
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input
python manage.py ensure_admin
python manage.py ledger_sync
