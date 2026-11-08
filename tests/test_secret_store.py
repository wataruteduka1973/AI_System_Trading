import os
import stat
import sys

import pytest
from app.security.secret_store import LocalEncryptedSecretStore
from cryptography.fernet import Fernet


def test_local_secret_store_encrypts_round_trip(tmp_path) -> None:
    store = LocalEncryptedSecretStore(tmp_path, Fernet.generate_key().decode("ascii"))

    secret_ref = store.put({"token": "very-sensitive"})

    encrypted_files = list(tmp_path.glob("*.bin"))
    assert len(encrypted_files) == 1
    assert b"very-sensitive" not in encrypted_files[0].read_bytes()
    assert store.get(secret_ref) == {"token": "very-sensitive"}

    encrypted_value = store.encrypt_text("101-001-private")
    assert "101-001-private" not in encrypted_value
    assert store.decrypt_text(encrypted_value) == "101-001-private"

    store.delete(secret_ref)
    assert not encrypted_files[0].exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_secret_files_are_readable_by_their_owner_only(tmp_path) -> None:
    root = tmp_path / "secrets"
    root.mkdir(mode=0o755)
    store = LocalEncryptedSecretStore(root, Fernet.generate_key().decode("ascii"))
    previous = os.umask(0)  # a permissive umask must not widen what the store asks for
    try:
        store.put({"token": "very-sensitive"})
    finally:
        os.umask(previous)

    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    (secret_file,) = root.glob("*.bin")
    assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600
