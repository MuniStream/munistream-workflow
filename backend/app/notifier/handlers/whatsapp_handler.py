"""WhatsApp delivery via the munistream-baileys microservice."""
import base64
import logging
from typing import Any, Dict

import httpx

from .base import (
    HandlerResult,
    NotificationHandler,
    OutboundMessage,
    PermanentDeliveryError,
    TransientDeliveryError,
)

logger = logging.getLogger(__name__)


class WhatsAppHandler(NotificationHandler):
    channel = "whatsapp"

    async def send(self, message: OutboundMessage) -> HandlerResult:
        creds = message.channel_credentials or {}
        base_url = creds.get("base_url")
        api_key = creds.get("api_key")
        tenant_id = message.tenant_id or creds.get("tenant_id")

        if not base_url:
            raise PermanentDeliveryError("baileys base_url no configurado")
        if not tenant_id:
            raise PermanentDeliveryError("tenant_id requerido para WhatsApp")

        url = f"{base_url.rstrip('/')}/send"
        headers = {"X-API-Key": api_key} if api_key else {}
        payloads = [{"tenant_id": tenant_id, "to": message.recipient, "body": message.body}]
        # Each document travels as its own message, after the text.
        for attachment in message.attachments:
            payloads.append({
                "tenant_id": tenant_id,
                "to": message.recipient,
                "document": {
                    "data_base64": base64.b64encode(attachment.data).decode(),
                    "filename": attachment.filename,
                    "mimetype": attachment.content_type,
                },
            })

        message_ids = []
        async with httpx.AsyncClient(timeout=60) as client:
            for index, payload in enumerate(payloads):
                try:
                    response = await client.post(url, json=payload, headers=headers)
                except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
                    raise TransientDeliveryError(f"baileys no accesible: {exc}") from exc
                except httpx.HTTPError as exc:
                    raise TransientDeliveryError(f"baileys HTTP error: {exc}") from exc
                if response.status_code == 200:
                    data = self._safe_json(response)
                    if isinstance(data, dict) and data.get("messageId"):
                        message_ids.append(data["messageId"])
                    continue
                # Once the text went out, retrying would duplicate the notice: a
                # document failing after the text is a permanent failure.
                self._raise_for(response, permanent=index > 0)

        return HandlerResult(
            success=True,
            provider_reference=",".join(message_ids) or None,
        )

    def _raise_for(self, response: httpx.Response, permanent: bool = False) -> None:
        data = self._safe_json(response)
        detail = data.get("error") if isinstance(data, dict) else response.text

        if response.status_code == 400:
            raise PermanentDeliveryError(f"baileys 400: {detail}")
        if response.status_code == 401 or response.status_code == 403:
            raise PermanentDeliveryError(f"baileys auth rechazada: {detail}")
        if response.status_code == 404:
            raise PermanentDeliveryError(f"baileys recurso no encontrado: {detail}")
        error = TransientDeliveryError
        if permanent:
            error = PermanentDeliveryError
        if response.status_code == 503:
            raise error(f"baileys sesión no disponible: {detail}")
        raise error(f"baileys {response.status_code}: {detail}")

    @staticmethod
    def _safe_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return {}
