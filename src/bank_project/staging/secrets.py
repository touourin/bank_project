"""Encrypted source credentials for durable jobs; key stays in the private data volume."""

import fcntl
import json
import os

from cryptography.fernet import Fernet


def cipher(data_dir):
    path = data_dir / "staging" / "credentials.key"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Serialize creation across API workers before any process reads the key.
    with (path.parent / "credentials.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as file:
                file.write(Fernet.generate_key())
                file.flush()
                os.fsync(file.fileno())
        return Fernet(path.read_bytes())


def encrypt_connection(data_dir, payload):
    return cipher(data_dir).encrypt(json.dumps(payload, ensure_ascii=False).encode()).decode()


def decrypt_connection(data_dir, value):
    return json.loads(cipher(data_dir).decrypt(value.encode()))
