from app.services.secrets import LocalEncryptedSecretStore
from cryptography.fernet import Fernet


def test_local_secret_store_encrypts_round_trip(tmp_path) -> None:
    store = LocalEncryptedSecretStore(tmp_path, Fernet.generate_key().decode("ascii"))

    secret_ref = store.put({"token": "very-sensitive"})

    encrypted_files = list(tmp_path.glob("*.bin"))
    assert len(encrypted_files) == 1
    assert b"very-sensitive" not in encrypted_files[0].read_bytes()
    assert store.get(secret_ref) == {"token": "very-sensitive"}

    store.delete(secret_ref)
    assert not encrypted_files[0].exists()
