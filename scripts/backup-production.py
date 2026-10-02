#!/usr/bin/env python3
"""Consistent daily backups of the co-hosted MVP services.

Run as root from a systemd timer. The Git repository intentionally excludes
database backups and environment files.
"""

import hashlib
import os
import shutil
import sqlite3
import subprocess
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


BACKUP_ROOT = Path(os.environ.get('QUALITY_BACKUP_ROOT', '/opt/quality-backups')).resolve()
OFFSITE_ROOT = BACKUP_ROOT / 'offsite'
OFFSITE_PUBLIC_KEY = Path('/opt/quality/offsite-public-v2.key')
QUALITY_SQLITE = Path('/opt/quality/data/db.sqlite3')
REQUESTS_SQLITE = Path('/opt/applicationsys/instance/app.db')
ARCHIVE_ROOTS = (
    Path('/opt/quality'),
    Path('/opt/onco'),
    Path('/opt/applicationsys'),
    Path('/var/lib/docker/volumes/deploy_caddy_data/_data'),
)
EXCLUDED_NAMES = {'.git', '.venv', 'node_modules', '__pycache__'}


def include(path):
    if any(part in EXCLUDED_NAMES for part in path.parts):
        return False
    if path.parts[:4] == ('opt', 'quality', 'data', 'postgres'):
        return False
    if path.parts[:3] == ('opt', 'quality', 'data') and path.name.startswith('migration-'):
        return False
    if path.as_posix() in {
        'opt/quality/data/db.sqlite3',
        'opt/quality/data/db.sqlite3-wal',
        'opt/quality/data/db.sqlite3-shm',
        'opt/applicationsys/instance/app.db',
        'opt/applicationsys/instance/app.db-wal',
        'opt/applicationsys/instance/app.db-shm',
    }:
        return False
    return path.suffix not in {'.log', '.pyc'}


def quality_uses_postgres():
    result = subprocess.run(
        ['docker', 'inspect', '--format', '{{range .Config.Env}}{{println .}}{{end}}', 'quality_app'],
        capture_output=True, text=True, check=True,
    )
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if values.get('USE_SQLITE') not in {'0', '1'}:
        raise RuntimeError('Cannot determine the active quality database backend')
    return values['USE_SQLITE'] == '0'


def backup_sqlite(source, target):
    if not source.is_file():
        raise FileNotFoundError(source)
    with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as origin:
        with sqlite3.connect(target) as destination:
            origin.backup(destination)
            if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError(f'Integrity check failed for {source}')
            if destination.execute('PRAGMA journal_mode=DELETE').fetchone()[0] != 'delete':
                raise RuntimeError(f'Could not checkpoint {source}')
    if any(Path(f'{target}{suffix}').exists() for suffix in ('-wal', '-shm')):
        raise RuntimeError(f'SQLite sidecar remained after backup: {target}')


def backup_postgres(target):
    command = [
        'docker', 'exec', 'quality_db', 'sh', '-c',
        'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc',
    ]
    with target.open('wb') as destination:
        result = subprocess.run(command, stdout=destination, stderr=subprocess.PIPE, check=False)
    if result.returncode or not target.stat().st_size:
        raise RuntimeError(f'PostgreSQL dump failed: {result.stderr.decode(errors="replace")}')
    with target.open('rb') as source:
        result = subprocess.run(
            ['docker', 'exec', '-i', 'quality_db', 'pg_restore', '--list'],
            stdin=source, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=False,
        )
    if result.returncode:
        raise RuntimeError(f'PostgreSQL dump verification failed: {result.stderr.decode(errors="replace")}')


def main():
    os.umask(0o077)
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    if shutil.disk_usage(BACKUP_ROOT).free < 512 * 1024 * 1024:
        raise RuntimeError('Less than 512 MiB free: backup would risk filling the VPS disk')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    pending = BACKUP_ROOT / f'.pending-{stamp}'
    final = BACKUP_ROOT / f'daily-{stamp}'
    pending.mkdir(mode=0o700)
    try:
        if quality_uses_postgres():
            backup_postgres(pending / 'quality.pgcustom')
        else:
            backup_sqlite(QUALITY_SQLITE, pending / 'quality.sqlite3')
        backup_sqlite(REQUESTS_SQLITE, pending / 'requests.sqlite3')
        with tarfile.open(pending / 'services.tar.gz', 'w:gz') as archive:
            for root in ARCHIVE_ROOTS:
                if not root.exists():
                    raise FileNotFoundError(root)
                archive.add(root, arcname=root.relative_to('/'), filter=lambda info: info if include(Path(info.name)) else None)
        with (pending / 'SHA256SUMS').open('w', encoding='ascii') as manifest:
            for file in sorted(pending.iterdir()):
                if file.name == 'SHA256SUMS':
                    continue
                with file.open('rb') as source:
                    digest = hashlib.file_digest(source, 'sha256').hexdigest()
                manifest.write(f'{digest}  {file.name}\n')
        pending.rename(final)
    except Exception:
        shutil.rmtree(pending)
        raise
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    for old in BACKUP_ROOT.glob('daily-*'):
        if not old.is_dir() or old.resolve().parent != BACKUP_ROOT:
            continue
        if datetime.fromtimestamp(old.stat().st_mtime, timezone.utc) < cutoff:
            shutil.rmtree(old)
    if OFFSITE_PUBLIC_KEY.is_file():
        from offsite_public import encrypt

        OFFSITE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
        encrypted = OFFSITE_ROOT / f'oncomap-abai-{stamp}.oncoenc'
        encrypt(OFFSITE_PUBLIC_KEY, final, encrypted)
        for old in OFFSITE_ROOT.glob('oncomap-abai-*.oncoenc'):
            if not old.is_file() or old.resolve().parent != OFFSITE_ROOT:
                continue
            if datetime.fromtimestamp(old.stat().st_mtime, timezone.utc) < cutoff:
                old.unlink()
        print(encrypted)
    print(final)


if __name__ == '__main__':
    main()
