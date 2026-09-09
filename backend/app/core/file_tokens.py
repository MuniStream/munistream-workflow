"""Tokens de descarga de corta vida para objetos de S3.

``GET /files/download/{s3_key}`` se sirve a veces desde un ancla del navegador,
donde no cabe una cabecera ``Authorization``. Historicamente eso se resolvio
dejando la ruta abierta, lo que convertia la s3_key en la unica credencial de
todo el bucket de subidas.

En su lugar, la autorizacion ocurre en un endpoint con sesion
(``POST /files/grant``), que comprueba que el archivo pertenezca al recurso que
el usuario puede ver y emite un token firmado con caducidad corta. El token es
autocontenido: no se guarda nada en la base de datos.
"""

import base64
import hashlib
import hmac
import time
from typing import Optional, Tuple

from .config import settings


def _sign(payload: str) -> str:
    mac = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(mac).decode("ascii").rstrip("=")


def issue_token(s3_key: str, principal_id: str, ttl_seconds: Optional[int] = None) -> Tuple[str, int]:
    """Emite un token para *una* s3_key concreta. Devuelve ``(token, expira_en)``.

    El principal va dentro de la firma para que un token emitido a un usuario no
    sea reutilizable como credencial generica si se filtra un enlace.
    """
    ttl = ttl_seconds if ttl_seconds is not None else settings.FILES_DOWNLOAD_TOKEN_TTL_SECONDS
    expires_at = int(time.time()) + int(ttl)
    payload = f"{s3_key}|{expires_at}|{principal_id}"
    return f"{expires_at}.{principal_id}.{_sign(payload)}", expires_at


def verify_token(token: str, s3_key: str) -> bool:
    """Valida firma y caducidad. Comparacion en tiempo constante."""
    if not token:
        return False

    parts = token.split(".", 2)
    if len(parts) != 3:
        return False

    raw_expires, principal_id, signature = parts
    try:
        expires_at = int(raw_expires)
    except ValueError:
        return False

    if expires_at < int(time.time()):
        return False

    expected = _sign(f"{s3_key}|{expires_at}|{principal_id}")
    return hmac.compare_digest(expected, signature)
