"""Fernet encryption for dashboard-managed Wi-Fi passwords. Never log plaintext or keys."""

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from app.config import settings

WIFI_KEY_NOT_CONFIGURED = "wifi_secret_key_not_configured"


def _fernet() -> Fernet | None:
    key = settings.ECHOSENSE_WIFI_SECRET_KEY
    if not key:
        return None
    try:
        return Fernet(key.strip().encode("ascii"))
    except (ValueError, TypeError, UnicodeEncodeError):
        return None


def encrypt_wifi_password(password: str) -> str:
    fernet = _fernet()
    if fernet is None:
        raise HTTPException(status_code=503, detail=WIFI_KEY_NOT_CONFIGURED)
    return fernet.encrypt(password.encode("utf-8")).decode("ascii")


def decrypt_wifi_password(token: str) -> str | None:
    """Plaintext, or None when the key is missing or the ciphertext does not match it."""

    fernet = _fernet()
    if fernet is None:
        return None
    try:
        return fernet.decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        return None
