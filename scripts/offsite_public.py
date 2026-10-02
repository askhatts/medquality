"""Public-key encrypted offsite backups (server gets only the public key).

The private X25519 key is itself protected by the existing local recovery key.
Format: magic, ephemeral public key, HKDF salt, GCM nonce, ciphertext, GCM tag.
"""

import argparse
import hashlib
import os
import shutil
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from offsite_backup import CHUNK, check_zip, expected_files, read_key, temporary_directory


MAGIC = b'ONCOBKP2'
KEY_MAGIC = b'ONCOKEY2'
SALT_SIZE = 32
NONCE_SIZE = 12
TAG_SIZE = 16
HEADER_SIZE = len(MAGIC) + 32 + SALT_SIZE + NONCE_SIZE


def derive_key(private_key, peer_public, salt):
    shared = private_key.exchange(peer_public)
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=MAGIC).derive(shared)


def create_keys(recovery_key_path, public_path, protected_private_path):
    public_path = Path(public_path)
    protected_private_path = Path(protected_private_path)
    if public_path.exists() or protected_private_path.exists():
        raise FileExistsError('An offsite key file already exists')
    private = x25519.X25519PrivateKey.generate()
    raw_private = private.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    raw_public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    nonce = os.urandom(NONCE_SIZE)
    protected = KEY_MAGIC + nonce + AESGCM(read_key(recovery_key_path)).encrypt(
        nonce, raw_private, KEY_MAGIC,
    )
    with public_path.open('xb') as target:
        target.write(raw_public)
    try:
        with protected_private_path.open('xb') as target:
            target.write(protected)
    except BaseException:
        public_path.unlink(missing_ok=True)
        raise
    return public_path, protected_private_path


def load_private(protected_private_path, recovery_key_path):
    data = Path(protected_private_path).read_bytes()
    if len(data) != len(KEY_MAGIC) + NONCE_SIZE + 32 + TAG_SIZE or not data.startswith(KEY_MAGIC):
        raise ValueError('Unknown private-key format')
    nonce = data[len(KEY_MAGIC):len(KEY_MAGIC) + NONCE_SIZE]
    raw = AESGCM(read_key(recovery_key_path)).decrypt(
        nonce, data[len(KEY_MAGIC) + NONCE_SIZE:], KEY_MAGIC,
    )
    return x25519.X25519PrivateKey.from_private_bytes(raw)


def verify_source(source_dir):
    source_dir = Path(source_dir)
    names = expected_files(path.name for path in source_dir.iterdir() if path.is_file())
    manifest = (source_dir / 'SHA256SUMS').read_text(encoding='ascii')
    expected = {}
    for line in manifest.splitlines():
        digest, separator, name = line.partition('  ')
        if not separator or len(digest) != 64:
            raise ValueError('Invalid checksum manifest')
        expected[name] = digest
    if set(expected) != names - {'SHA256SUMS'}:
        raise ValueError('Checksum manifest does not match files')
    for name, digest in expected.items():
        with (source_dir / name).open('rb') as source:
            actual = hashlib.file_digest(source, 'sha256').hexdigest()
        if actual != digest:
            raise ValueError(f'Checksum mismatch: {name}')
    return names


def encrypt(public_path, source_dir, output_path):
    output_path = Path(output_path)
    if output_path.exists():
        raise FileExistsError(output_path)
    names = verify_source(source_dir)
    raw_public = Path(public_path).read_bytes()
    if len(raw_public) != 32:
        raise ValueError('X25519 public key must have 32 bytes')
    ephemeral = x25519.X25519PrivateKey.generate()
    ephemeral_public = ephemeral.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    salt = os.urandom(SALT_SIZE)
    nonce = os.urandom(NONCE_SIZE)
    header = MAGIC + ephemeral_public + salt + nonce
    key = derive_key(ephemeral, x25519.X25519PublicKey.from_public_bytes(raw_public), salt)
    with temporary_directory() as work:
        packed = work / 'backup.zip'
        with zipfile.ZipFile(packed, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name in sorted(names):
                archive.write(Path(source_dir) / name, arcname=name)
        check_zip(packed)
        encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        encryptor.authenticate_additional_data(header)
        pending = output_path.with_name(output_path.name + '.pending')
        try:
            with packed.open('rb') as source, pending.open('xb') as target:
                target.write(header)
                for block in iter(lambda: source.read(CHUNK), b''):
                    target.write(encryptor.update(block))
                target.write(encryptor.finalize())
                target.write(encryptor.tag)
                target.flush()
                os.fsync(target.fileno())
            pending.replace(output_path)
        finally:
            pending.unlink(missing_ok=True)
    return output_path


def decrypt_to_zip(encrypted_path, private_key, packed):
    encrypted_path = Path(encrypted_path)
    length = encrypted_path.stat().st_size
    if length < HEADER_SIZE + TAG_SIZE:
        raise ValueError('Encrypted backup is too short')
    with encrypted_path.open('rb') as source:
        header = source.read(HEADER_SIZE)
        if not header.startswith(MAGIC):
            raise ValueError('Unknown encrypted backup format')
        offset = len(MAGIC)
        ephemeral_public = x25519.X25519PublicKey.from_public_bytes(header[offset:offset + 32])
        salt = header[offset + 32:offset + 32 + SALT_SIZE]
        nonce = header[-NONCE_SIZE:]
        source.seek(length - TAG_SIZE)
        tag = source.read(TAG_SIZE)
        source.seek(HEADER_SIZE)
        key = derive_key(private_key, ephemeral_public, salt)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(header)
        remaining = length - HEADER_SIZE - TAG_SIZE
        with Path(packed).open('wb') as destination:
            while remaining:
                block = source.read(min(CHUNK, remaining))
                if not block:
                    raise ValueError('Encrypted backup is truncated')
                destination.write(decryptor.update(block))
                remaining -= len(block)
            destination.write(decryptor.finalize())
    check_zip(packed)


def verify(encrypted_path, protected_private_path, recovery_key_path):
    private = load_private(protected_private_path, recovery_key_path)
    with temporary_directory() as work:
        decrypt_to_zip(encrypted_path, private, work / 'backup.zip')
    return True


def restore(encrypted_path, protected_private_path, recovery_key_path, output_dir):
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise FileExistsError(output_dir)
    private = load_private(protected_private_path, recovery_key_path)
    with temporary_directory(output_dir.parent) as work:
        packed = work / 'backup.zip'
        decrypt_to_zip(encrypted_path, private, packed)
        staged = work / 'restored'
        staged.mkdir()
        with zipfile.ZipFile(packed) as archive:
            for name in expected_files(archive.namelist()):
                with archive.open(name) as source, (staged / name).open('wb') as target:
                    shutil.copyfileobj(source, target, CHUNK)
        staged.rename(output_dir)
    return output_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    keygen = sub.add_parser('keygen')
    keygen.add_argument('recovery_key')
    keygen.add_argument('public_key')
    keygen.add_argument('protected_private_key')
    create = sub.add_parser('encrypt')
    create.add_argument('public_key')
    create.add_argument('source_dir')
    create.add_argument('output_file')
    check = sub.add_parser('verify')
    check.add_argument('encrypted_file')
    check.add_argument('protected_private_key')
    check.add_argument('recovery_key')
    extract = sub.add_parser('restore')
    extract.add_argument('encrypted_file')
    extract.add_argument('protected_private_key')
    extract.add_argument('recovery_key')
    extract.add_argument('output_dir')
    args = parser.parse_args()
    if args.action == 'keygen':
        print(create_keys(args.recovery_key, args.public_key, args.protected_private_key))
    elif args.action == 'encrypt':
        print(encrypt(args.public_key, args.source_dir, args.output_file))
    elif args.action == 'verify':
        verify(args.encrypted_file, args.protected_private_key, args.recovery_key)
        print('Encrypted archive and checksums verified')
    else:
        print(restore(args.encrypted_file, args.protected_private_key, args.recovery_key, args.output_dir))


if __name__ == '__main__':
    main()
