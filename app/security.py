from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class CredentialVault:
    def __init__(self, secret: str) -> None:
        digest = hashlib.sha256(secret.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, credentials: dict[str, Any]) -> str:
        payload = json.dumps(credentials, separators=(",", ":")).encode("utf-8")
        return self._fernet.encrypt(payload).decode("ascii")

    def decrypt(self, encrypted: str) -> dict[str, Any]:
        try:
            payload = self._fernet.decrypt(encrypted.encode("ascii"))
        except InvalidToken as exc:
            raise ValueError("Stored broker credentials cannot be decrypted") from exc
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError("Invalid stored broker credentials")
        return value
