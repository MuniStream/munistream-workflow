"""Fernet-based encryption for notification channel credentials.

The master key comes from the `NOTIFICATION_SECRET_KEY` environment setting
(urlsafe base64, 32 bytes). Stored values are opaque strings; callers receive
and provide Python dicts.
"""
import json
import os
from functools import lru_cache
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet, InvalidToken

from ..core.config import settings


class EncryptionNotConfigured(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = settings.NOTIFICATION_SECRET_KEY or os.getenv("NOTIFICATION_SECRET_KEY")
    if not key:
        raise EncryptionNotConfigured(
            "NOTIFICATION_SECRET_KEY is not configured. Generate with: "
            "python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_credentials(data: Dict[str, Any]) -> str:
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
    return _fernet().encrypt(payload).decode()


def decrypt_credentials(token: Optional[str]) -> Dict[str, Any]:
    if not token:
        return {}
    try:
        raw = _fernet().decrypt(token.encode())
    except InvalidToken as exc:
        raise EncryptionNotConfigured(
            "Stored credentials cannot be decrypted. Rotate NOTIFICATION_SECRET_KEY or re-enter credentials."
        ) from exc
    return json.loads(raw.decode())


def mask_credentials(data: Dict[str, Any]) -> Dict[str, Any]:
    """Return a safe representation with passwords/keys masked for the admin UI."""
    sensitive = {"password", "api_key", "smtp_password", "secret", "token"}
    masked: Dict[str, Any] = {}
    for key, value in data.items():
        if key.lower() in sensitive and value:
            masked[key] = "****"
        else:
            masked[key] = value
    return masked
