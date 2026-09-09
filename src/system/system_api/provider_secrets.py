"""Encrypted storage for provider credentials."""

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

DEFAULT_KEY_FILE = ".cache/provider-secrets.key"


class ProviderSecretError(RuntimeError):
    pass


class ProviderSecretStore:
    def __init__(self, cipher: Fernet) -> None:
        self._cipher = cipher

    @classmethod
    def from_config(cls) -> "ProviderSecretStore":
        return cls.from_path(
            Path(os.getenv("PROVIDER_SECRETS_KEY_FILE", DEFAULT_KEY_FILE))
        )

    @classmethod
    def from_path(cls, path: Path) -> "ProviderSecretStore":
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                cls._create_key_file(path)
            key = path.read_bytes().strip()
            return cls(Fernet(key))
        except (OSError, ValueError) as exc:
            raise ProviderSecretError(
                "Provider secret key is unavailable or invalid"
            ) from exc

    @staticmethod
    def _create_key_file(path: Path) -> None:
        key = Fernet.generate_key() + b"\n"
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return
        with os.fdopen(descriptor, "wb") as key_file:
            key_file.write(key)

    def encrypt(self, value: str) -> str:
        if not value:
            raise ProviderSecretError("Provider API key is empty")
        return self._cipher.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str | None) -> str:
        if not token:
            raise ProviderSecretError("Provider API key is not configured")
        try:
            return self._cipher.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError) as exc:
            raise ProviderSecretError(
                "Stored provider API key cannot be decrypted"
            ) from exc
