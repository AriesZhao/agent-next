"""Credential handling for data-source connections (§15.4).

Passwords are stored Fernet-encrypted (mirroring ``infra/connectors/crypto.py``)
under a dedicated ``data_source_fernet`` key and are **never** returned in a
public payload — reads always mask them. Decryption happens per query so the
plaintext does not linger in memory.
"""

from __future__ import annotations

from typing import Any

from cryptography.fernet import Fernet

_DS_FERNET_KEY = "data_source_fernet"


def _get_fernet(secret_repo: Any) -> Fernet:
    raw = secret_repo.get_or_create(_DS_FERNET_KEY, Fernet.generate_key)
    return Fernet(raw)


def encrypt_password(secret_repo: Any, password: str) -> bytes | None:
    if not password:
        return None
    return _get_fernet(secret_repo).encrypt(password.encode("utf-8"))


def decrypt_password(secret_repo: Any, blob: bytes | None) -> str:
    if not blob:
        return ""
    return _get_fernet(secret_repo).decrypt(blob).decode("utf-8")
