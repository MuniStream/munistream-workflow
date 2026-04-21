"""WhatsApp delivery via the munistream-baileys microservice."""
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
        payload = {
            "tenant_id": tenant_id,
            "to": message.recipient,
            "body": message.body,
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(url, json=payload, headers=headers)
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
            raise TransientDeliveryError(f"baileys no accesible: {exc}") from exc
        except httpx.HTTPError as exc:
            raise TransientDeliveryError(f"baileys HTTP error: {exc}") from exc

        if response.status_code == 200:
            data = self._safe_json(response)
            return HandlerResult(
                success=True,
                provider_reference=data.get("messageId") if isinstance(data, dict) else None,
            )

        data = self._safe_json(response)
        detail = data.get("error") if isinstance(data, dict) else response.text

        if response.status_code == 400:
            raise PermanentDeliveryError(f"baileys 400: {detail}")
        if response.status_code == 401 or response.status_code == 403:
            raise PermanentDeliveryError(f"baileys auth rechazada: {detail}")
        if response.status_code == 404:
            raise PermanentDeliveryError(f"baileys recurso no encontrado: {detail}")
        if response.status_code == 503:
            raise TransientDeliveryError(f"baileys sesión no disponible: {detail}")

        raise TransientDeliveryError(f"baileys {response.status_code}: {detail}")

    @staticmethod
    def _safe_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return {}
