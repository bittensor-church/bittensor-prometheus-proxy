#!/bin/sh
set -eu
netuid=${1:?}
python /wallet_init.py
./prometheus-cleanup.sh
PROMETHEUS_EXPORT_MIGRATIONS=0 ./manage.py wait_for_database --timeout 60
PROMETHEUS_EXPORT_MIGRATIONS=0 ./manage.py migrate --no-input
./manage.py shell -c "from pathlib import Path; from project.core.models import Validator; ss58 = Path('/wallets/ss58').read_text().strip(); Validator.objects.update_or_create(public_key=ss58, netuid=${netuid}, defaults={'active': True, 'debug': True})"
exec gunicorn -c gunicorn.conf.py --timeout 120
