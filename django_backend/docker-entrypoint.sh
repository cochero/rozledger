#!/bin/sh
set -e

python - <<'PY'
import os
import socket
import time

host = os.getenv("MYSQL_HOST", "mysql")
port = int(os.getenv("MYSQL_PORT", "3306"))
deadline = time.time() + 60

while time.time() < deadline:
    try:
        with socket.create_connection((host, port), timeout=3):
            print(f"MySQL is reachable at {host}:{port}")
            break
    except OSError:
        print(f"Waiting for MySQL at {host}:{port}...")
        time.sleep(2)
else:
    raise SystemExit(f"MySQL was not reachable at {host}:{port}")
PY

python manage.py migrate --noinput
python /app/build_scripts/generate_content.py
python manage.py collectstatic --noinput

if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_EMAIL" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
  python manage.py createsuperuser --noinput || true
fi

exec "$@"
