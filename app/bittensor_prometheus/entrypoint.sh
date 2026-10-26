#!/bin/sh
set -e
python /app/read_wallet_and_substitute_config.py
exec /bin/prometheus --config.file=/etc/prometheus/prometheus.yml "$@"