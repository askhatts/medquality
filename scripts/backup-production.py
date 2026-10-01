#!/usr/bin/env python3
"""Consistent daily backups of the co-hosted MVP services.

Run as root from a systemd timer. The Git repository intentionally excludes
database backups and environment files.
"""

import hashlib
import os
import shutil
import sqlite3
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


BACKUP_ROOT = Path(os.environ.get('QUALITY_BACKUP_ROOT', '/opt/quality-backups')).resolve()
DATABASES = {
    'quality.sqlite3': Path('/opt/quality/data/db.sqlite3'),
    'requests.sqlite3': Path('/opt/applicationsys/instance/app.db'),
}
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
    if path.as_posix() in {'opt/quality/data/db.sqlite3', 'opt/applicationsys/instance/app.db'}:
        return False
    return path.suffix not in {'.log', '.pyc'}


def main():
    os.umask(0o077)
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    pending = BACKUP_ROOT / f'.pending-{stamp}'
    final = BACKUP_ROOT / f'daily-{stamp}'
    pending.mkdir(mode=0o700)
    try:
        for name, source in DATABASES.items():
            if not source.is_file():
                raise FileNotFoundError(source)
            with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as origin:
                with sqlite3.connect(pending / name) as target:
                    origin.backup(target)
                    if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise RuntimeError(f'Integrity check failed for {name}')
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
    print(final)


if __name__ == '__main__':
    main()
