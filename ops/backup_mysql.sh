#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/rozledger}"
BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

cd "$APP_DIR"

read_env_var() {
  local name="$1"
  local file="django_backend/.env.docker"
  if [ -f "$file" ]; then
    grep -E "^${name}=" "$file" | tail -n 1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
  fi
}

MYSQL_DATABASE="${MYSQL_DATABASE:-$(read_env_var MYSQL_DATABASE)}"
MYSQL_USER="${MYSQL_USER:-$(read_env_var MYSQL_USER)}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:-$(read_env_var MYSQL_PASSWORD)}"

: "${MYSQL_DATABASE:?MYSQL_DATABASE is required}"
: "${MYSQL_USER:?MYSQL_USER is required}"
: "${MYSQL_PASSWORD:?MYSQL_PASSWORD is required}"

mkdir -p "$BACKUP_DIR"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_file="$BACKUP_DIR/${MYSQL_DATABASE}_${timestamp}.sql.gz"

docker compose exec -T mysql mysqldump \
  --single-transaction \
  --quick \
  --no-tablespaces \
  --default-character-set=utf8mb4 \
  -u"$MYSQL_USER" \
  -p"$MYSQL_PASSWORD" \
  "$MYSQL_DATABASE" | gzip > "$backup_file"

find "$BACKUP_DIR" -type f -name "${MYSQL_DATABASE}_*.sql.gz" -mtime +"$RETENTION_DAYS" -delete

echo "Backup written to $backup_file"
