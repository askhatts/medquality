"""Roll a compatible quality image via a temporary upstream, without restarting other apps.

Use only after a backup and successful isolated load test. Credentials stay in
VPS_DEPLOY_PASSWORD; the new image/compose must already exist on the VPS.
"""
import os
import sys
import time
import argparse
import re
import paramiko

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--artifacts', required=True, help='Existing private /opt/quality-loadtest-<stamp> directory')
args = parser.parse_args()
if not re.fullmatch(r'/opt/quality-loadtest-[0-9]{8}(?:_[0-9]{6})?', args.artifacts):
    parser.error('Expected an explicitly named load-test artifact directory')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
client = paramiko.SSHClient()
client.load_system_host_keys()
client.set_missing_host_key_policy(paramiko.RejectPolicy())
client.connect('109.248.199.76', username='root', password=os.environ['VPS_DEPLOY_PASSWORD'],
               timeout=15, look_for_keys=False, allow_agent=False)


def run(command):
    _, stdout, stderr = client.exec_command(command, timeout=180)
    output, error = stdout.read().decode(), stderr.read().decode()
    code = stdout.channel.recv_exit_status()
    if code:
        # Never emit environment values or the raw Caddyfile.
        raise RuntimeError(f'Rollout command failed ({code}): {error[-2000:]}')
    print(output.strip(), flush=True)
    return output


def ready(name):
    command = (f'docker exec {name} python -c "import urllib.request; '
               'r=urllib.request.Request(\'http://127.0.0.1:8000/quality/health/\', '
               'headers={\'Host\':\'oncomap-abai.kz\'}); '
               'assert urllib.request.urlopen(r,timeout=4).status==200"')
    for _ in range(30):
        try:
            run(command)
            return
        except RuntimeError:
            time.sleep(1)
    raise RuntimeError(f'{name} was not ready; keep the temporary upstream if it is serving')


try:
    run("test -z \"$(docker ps -aq --filter name='^quality_rollout$')\"")
    sftp = client.open_sftp()
    with sftp.open('/opt/onco/deploy/Caddyfile', 'rb') as source:
        caddy = source.read()
    original = b'reverse_proxy quality_app:8000'
    assert caddy.count(original) == 1, 'Unexpected Caddy routing; refusing automatic modification'
    with sftp.open(args.artifacts + '/Caddyfile.before-rollout', 'wb') as destination:
        destination.write(caddy)
    with sftp.open(args.artifacts + '/Caddyfile.rollout', 'wb') as destination:
        destination.write(caddy.replace(original, b'reverse_proxy quality_rollout:8000'))
    sftp.close()
    run('docker run -d --name quality_rollout --restart unless-stopped --network deploy_default '
        '--memory 1g --pids-limit 128 --log-opt max-size=10m --log-opt max-file=2 '
        '--env-file /opt/quality/.env -e POSTGRES_DB=quality -e POSTGRES_USER=quality '
        '-e POSTGRES_HOST=quality_db -e USE_SQLITE=0 -e DJANGO_DEBUG=0 '
        '-e DJANGO_ALLOWED_HOSTS=oncomap-abai.kz,www.oncomap-abai.kz '
        '-e DJANGO_CSRF_TRUSTED_ORIGINS=https://oncomap-abai.kz,https://www.oncomap-abai.kz '
        '-e MEDIA_ROOT=/data/media -v /opt/quality/data:/data quality-quality '
        'gunicorn config.wsgi:application --bind 0.0.0.0:8000 --worker-class gthread '
        '--workers 2 --threads 8 --timeout 60 --max-requests 0')
    ready('quality_rollout')
    run(f'docker cp {args.artifacts}/Caddyfile.rollout deploy-caddy-1:/tmp/quality-rollout.Caddyfile')
    run('docker exec deploy-caddy-1 caddy validate --config /tmp/quality-rollout.Caddyfile --adapter caddyfile')
    run('docker exec deploy-caddy-1 caddy reload --config /tmp/quality-rollout.Caddyfile --adapter caddyfile')
    print('Temporary upstream is now serving; existing sessions use the same database and secret.', flush=True)
    run('cd /opt/quality && docker compose -f docker-compose.quality.yml '
        'up -d --no-deps --wait --wait-timeout 90 --timeout 90 quality')
    ready('quality_app')
    run('docker exec deploy-caddy-1 caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile')
    print('Original routing restored to the new quality_app.', flush=True)
    run('docker stop -t 90 quality_rollout && docker rm quality_rollout')
    print('Rollout complete. Other application containers and PostgreSQL were not restarted.', flush=True)
finally:
    # On error do not remove the temporary serving container: it may be the
    # only healthy upstream. Diagnose and restore original routing deliberately.
    client.close()
