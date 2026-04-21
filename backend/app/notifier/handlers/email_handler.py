"""SMTP email delivery via aiosmtplib."""
import logging
from email.message import EmailMessage
from typing import Any, Dict

from .base import (
    HandlerResult,
    NotificationHandler,
    OutboundMessage,
    PermanentDeliveryError,
    TransientDeliveryError,
)

logger = logging.getLogger(__name__)


class EmailHandler(NotificationHandler):
    channel = "email"

    async def send(self, message: OutboundMessage) -> HandlerResult:
        import aiosmtplib  # lazy import so missing dep doesn't break module load

        creds = message.channel_credentials or {}
        host = creds.get("host")
        port = int(creds.get("port", 587))
        username = creds.get("username") or None
        password = creds.get("password") or None
        use_tls = bool(creds.get("use_tls", False))
        start_tls = bool(creds.get("start_tls", port == 587))

        if not host:
            raise PermanentDeliveryError("SMTP host no configurado")

        email = EmailMessage()
        email["From"] = message.from_address or username or "no-reply@localhost"
        email["To"] = message.recipient
        email["Subject"] = message.subject or ""
        email.set_content(message.body)

        try:
            response = await aiosmtplib.send(
                email,
                hostname=host,
                port=port,
                username=username,
                password=password,
                use_tls=use_tls,
                start_tls=start_tls if not use_tls else False,
                timeout=15,
            )
        except aiosmtplib.SMTPAuthenticationError as exc:
            raise PermanentDeliveryError(f"Autenticación SMTP rechazada: {exc}") from exc
        except aiosmtplib.SMTPRecipientsRefused as exc:
            raise PermanentDeliveryError(f"Destinatario rechazado por SMTP: {exc}") from exc
        except (aiosmtplib.SMTPConnectError, aiosmtplib.SMTPServerDisconnected, TimeoutError, OSError) as exc:
            raise TransientDeliveryError(f"Fallo transitorio SMTP: {exc}") from exc
        except aiosmtplib.SMTPException as exc:
            message_text = str(exc).lower()
            if "5." in message_text[:4]:
                raise PermanentDeliveryError(f"SMTP error permanente: {exc}") from exc
            raise TransientDeliveryError(f"SMTP error: {exc}") from exc

        provider_ref = None
        if isinstance(response, tuple) and len(response) == 2:
            _errors, server_response = response
            provider_ref = server_response.strip() if isinstance(server_response, str) else None

        logger.info("Email delivered to %s via %s:%s", message.recipient, host, port)
        return HandlerResult(success=True, provider_reference=provider_ref)
