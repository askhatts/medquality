#!/usr/bin/env bash
# Isolated database/container; no production passwords or results are changed.
set -euo pipefail
umask 077
stamp=${1:?Pass a unique UTC stamp: YYYYMMDD_HHMMSS}
[[ "$stamp" =~ ^[0-9]{8}_[0-9]{6}$ ]] || exit 2
db=quality_loadtest_$stamp
root=/opt/quality-loadtest-$stamp
test ! -e "$root"
test -z "$(docker ps -aq --filter name='^quality_loadtest$')"
test "$(docker exec quality_db psql -U quality -d postgres -Atc "SELECT count(*) FROM pg_database WHERE datname='$db'")" = 0
mkdir -m 700 "$root"
docker exec quality_db pg_dump -U quality -d quality -Fc > "$root/source.pgcustom"
docker exec quality_db psql -U quality -d quality -Atc 'SELECT (SELECT count(*) FROM auth_user), (SELECT count(*) FROM core_assignment), (SELECT count(*) FROM core_testattempt)' > "$root/live-before.txt"
docker exec quality_db createdb -U quality -O quality "$db"
docker exec -i quality_db pg_restore -U quality -d "$db" --no-owner --exit-on-error < "$root/source.pgcustom"
docker run -d --name quality_loadtest --network deploy_default --memory 1g --cpus 1.5 --pids-limit 128 \
  --log-opt max-size=10m --log-opt max-file=2 \
  --env-file /opt/quality/.env -e POSTGRES_DB="$db" -e POSTGRES_HOST=quality_db \
  -e POSTGRES_USER=quality -e USE_SQLITE=0 -e DJANGO_DEBUG=0 \
  -e DJANGO_ALLOWED_HOSTS=oncomap-abai.kz -e MEDIA_ROOT=/data/media \
  -v /opt/quality/scripts:/app/scripts:ro -v /opt/quality/data/media:/data/media:ro \
  quality-quality gunicorn config.wsgi:application --bind 0.0.0.0:8000 \
  --worker-class gthread --workers 2 --threads 8 --timeout 60 \
  --max-requests 0
docker exec -e DJANGO_SETTINGS_MODULE=config.settings quality_loadtest python /app/scripts/load_staff_seed.py initial
docker cp quality_loadtest:/tmp/load-manifest.json "$root/manifest.json"
chmod 600 "$root/manifest.json"
echo "Prepared isolated stand: $db"
