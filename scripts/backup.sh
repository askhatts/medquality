#!/bin/sh
set -eu
: "${POSTGRES_DB:?}" "${POSTGRES_USER:?}"
stamp=$(date +%F_%H%M%S)
mkdir -p /backups
pg_dump -h "${POSTGRES_HOST:-db}" -U "$POSTGRES_USER" -Fc "$POSTGRES_DB" > "/backups/db_${stamp}.dump"
tar -czf "/backups/media_${stamp}.tar.gz" -C /app media
find /backups -type f -mtime +14 -delete
