"""Encrypt-at-rest for OAuth refresh/access tokens. PRD §5.1, §8.1: non-negotiable."""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class TokenCipher:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("Token could not be decrypted — wrong key or corrupted value.") from exc
