import json
import os
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, status

from app.core.config import settings

SECRET_REF_PREFIX = "local-encrypted://"


class LocalEncryptedSecretStore:
    """Encrypted local secret storage for development environments."""

    def __init__(self, root: Path, encryption_key: str) -> None:
        self.root = root.resolve()
        try:
            self.cipher = Fernet(encryption_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("SECRET_ENCRYPTION_KEY must be a valid Fernet key") from exc

    def put(self, values: dict[str, str]) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        secret_id = uuid4().hex
        target = self.root / f"{secret_id}.bin"
        temporary = self.root / f".{secret_id}.tmp"
        encrypted = self.cipher.encrypt(json.dumps(values, sort_keys=True).encode("utf-8"))
        temporary.write_bytes(encrypted)
        os.replace(temporary, target)
        return f"{SECRET_REF_PREFIX}{secret_id}"

    def get(self, secret_ref: str) -> dict[str, str]:
        target = self._path_for(secret_ref)
        try:
            payload = self.cipher.decrypt(target.read_bytes())
        except (FileNotFoundError, InvalidToken) as exc:
            raise KeyError("Secret not found or cannot be decrypted") from exc
        value = json.loads(payload)
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in value.items()
        ):
            raise ValueError("Stored secret has an invalid format")
        return value

    def delete(self, secret_ref: str) -> None:
        self._path_for(secret_ref).unlink(missing_ok=True)

    def encrypt_text(self, value: str) -> str:
        return self.cipher.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt_text(self, value: str) -> str:
        try:
            return self.cipher.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError) as exc:
            raise ValueError("Encrypted value cannot be decrypted") from exc

    def _path_for(self, secret_ref: str) -> Path:
        if not secret_ref.startswith(SECRET_REF_PREFIX):
            raise ValueError("Unsupported secret reference")
        secret_id = secret_ref.removeprefix(SECRET_REF_PREFIX)
        if len(secret_id) != 32 or any(
            character not in "0123456789abcdef" for character in secret_id
        ):
            raise ValueError("Invalid secret reference")
        return self.root / f"{secret_id}.bin"


def get_secret_store() -> LocalEncryptedSecretStore:
    configured_key = settings.secret_encryption_key
    if configured_key is None or configured_key.get_secret_value() in {
        "",
        "replace-with-generated-fernet-key",
    }:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Local secret encryption is not configured",
        )
    try:
        return LocalEncryptedSecretStore(
            settings.secret_store_path, configured_key.get_secret_value()
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
