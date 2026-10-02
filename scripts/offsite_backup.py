"""Encrypt and verify an Oncomap backup before sending it off site.

The AES-256-GCM key is a separate local file. Never upload it with the backup.
The encrypted file format is: magic (8), nonce (12), ciphertext, tag (16).
"""

import argparse
import hashlib
import os
import secrets
import shutil
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


MAGIC = b'ONCOBKP1'
NONCE_SIZE = 12
TAG_SIZE = 16
CHUNK = 1024 * 1024
QUALITY_BACKUPS = ('quality.sqlite3', 'quality.pgcustom')
OTHER_FILES = ('requests.sqlite3', 'services.tar.gz', 'SHA256SUMS')


@contextmanager
def temporary_directory(parent=None):
    location = parent or os.environ.get('ONCO_BACKUP_TEMP_DIR') or tempfile.gettempdir()
    location = Path(location).resolve()
    if not location.is_dir():
        raise NotADirectoryError(location)
    work = location / ('.onco-backup-' + secrets.token_hex(12))
    work.mkdir()
    if os.name != 'nt':
        work.chmod(0o700)
    try:
        yield work
    finally:
        if work.resolve().parent != location:
            raise RuntimeError('Refusing to remove a temporary folder outside its parent')
        shutil.rmtree(work)


def expected_files(names):
    names = set(names)
    selected = names.intersection(QUALITY_BACKUPS)
    if len(selected) != 1 or names != selected.union(OTHER_FILES):
        raise ValueError('Backup contains unexpected or missing files')
    return names


def read_key(path):
    key = Path(path).read_bytes()
    if len(key) != 32:
        raise ValueError('Recovery key must contain exactly 32 bytes')
    return key


def ensure_key(path):
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(descriptor, 'wb') as target:
                target.write(os.urandom(32))
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    return read_key(path)


def check_zip(path):
    with zipfile.ZipFile(path) as archive:
        names = expected_files(archive.namelist())
        manifest = archive.read('SHA256SUMS').decode('ascii')
        expected = {}
        for line in manifest.splitlines():
            digest, _, name = line.partition('  ')
            expected[name] = digest
        if set(expected) != names - {'SHA256SUMS'}:
            raise ValueError('Backup checksum manifest does not match contents')
        for name in names - {'SHA256SUMS'}:
            digest = hashlib.sha256()
            with archive.open(name) as source:
                for block in iter(lambda: source.read(CHUNK), b''):
                    digest.update(block)
            if digest.hexdigest() != expected.get(name):
                raise ValueError(f'Checksum mismatch: {name}')
        if archive.testzip():
            raise ValueError('Backup ZIP is damaged')


def encrypt(source_dir, key_file, output_file):
    source_dir = Path(source_dir)
    output_file = Path(output_file)
    if output_file.exists():
        raise FileExistsError(output_file)
    key = ensure_key(key_file)
    files = expected_files(path.name for path in source_dir.iterdir() if path.is_file())
    for name in files:
        if not (source_dir / name).is_file():
            raise FileNotFoundError(source_dir / name)
    with temporary_directory() as work:
        packed = Path(work) / 'backup.zip'
        with zipfile.ZipFile(packed, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name in sorted(files):
                archive.write(source_dir / name, arcname=name)
        check_zip(packed)
        nonce = os.urandom(NONCE_SIZE)
        encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        encryptor.authenticate_additional_data(MAGIC)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        pending = output_file.with_name(output_file.name + '.pending')
        try:
            with packed.open('rb') as source, pending.open('xb') as target:
                target.write(MAGIC + nonce)
                for block in iter(lambda: source.read(CHUNK), b''):
                    target.write(encryptor.update(block))
                target.write(encryptor.finalize())
                target.write(encryptor.tag)
                target.flush()
                os.fsync(target.fileno())
            pending.replace(output_file)
        finally:
            pending.unlink(missing_ok=True)
    return output_file


def decrypt_to_zip(encrypted_file, key_file, packed):
    encrypted_file = Path(encrypted_file)
    key = read_key(key_file)
    length = encrypted_file.stat().st_size
    if length < len(MAGIC) + NONCE_SIZE + TAG_SIZE:
        raise ValueError('Encrypted backup is too short')
    with encrypted_file.open('rb') as source:
        header = source.read(len(MAGIC) + NONCE_SIZE)
        if header[:len(MAGIC)] != MAGIC:
            raise ValueError('Unknown encrypted backup format')
        source.seek(length - TAG_SIZE)
        tag = source.read(TAG_SIZE)
        source.seek(len(header))
        decryptor = Cipher(algorithms.AES(key), modes.GCM(header[len(MAGIC):], tag)).decryptor()
        decryptor.authenticate_additional_data(MAGIC)
        remaining = length - len(header) - TAG_SIZE
        with packed.open('wb') as target:
            while remaining:
                block = source.read(min(CHUNK, remaining))
                if not block:
                    raise ValueError('Encrypted backup is truncated')
                target.write(decryptor.update(block))
                remaining -= len(block)
            target.write(decryptor.finalize())
        check_zip(packed)


def verify(encrypted_file, key_file):
    with temporary_directory() as work:
        decrypt_to_zip(encrypted_file, key_file, Path(work) / 'backup.zip')
    return True


def restore(encrypted_file, key_file, output_dir):
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise FileExistsError(output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with temporary_directory(output_dir.parent) as work:
        packed = Path(work) / 'backup.zip'
        decrypt_to_zip(encrypted_file, key_file, packed)
        staged = Path(work) / 'restored'
        staged.mkdir()
        with zipfile.ZipFile(packed) as archive:
            for name in expected_files(archive.namelist()):
                with archive.open(name) as source, (staged / name).open('wb') as target:
                    shutil.copyfileobj(source, target, CHUNK)
        staged.rename(output_dir)
    return output_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest='action', required=True)
    create = subcommands.add_parser('encrypt')
    create.add_argument('source_dir', type=Path)
    create.add_argument('key_file', type=Path)
    create.add_argument('output_file', type=Path)
    check = subcommands.add_parser('verify')
    check.add_argument('encrypted_file', type=Path)
    check.add_argument('key_file', type=Path)
    extract = subcommands.add_parser('restore')
    extract.add_argument('encrypted_file', type=Path)
    extract.add_argument('key_file', type=Path)
    extract.add_argument('output_dir', type=Path)
    args = parser.parse_args()
    if args.action == 'encrypt':
        print(encrypt(args.source_dir, args.key_file, args.output_file))
        verify(args.output_file, args.key_file)
    elif args.action == 'verify':
        verify(args.encrypted_file, args.key_file)
    else:
        print(restore(args.encrypted_file, args.key_file, args.output_dir))
    print('Backup decrypts and all checksums match')


if __name__ == '__main__':
    main()
