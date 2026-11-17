"""
Client of DocumentIA, the service that reads documents (IDs, proof of address,
tax certificates, deeds, permits, invoices...) and returns their data as JSON.

What each document contains, how it is read and what is checked lives in
DocumentIA, not here: it publishes a catalog of document types with the JSON
Schema of their data (`GET /v1/documentos`), reads a document asynchronously
(`POST /v1/documentos/{type}` → job) and answers with
`{tipo, version, datos, es_este_tipo, legible, problemas}` when the job is done
(`GET /v1/jobs/{id}`).

Authentication is a Keycloak client of DocumentIA's realm (client credentials);
the token and the catalog are cached per process, never on an operator (one
operator object serves every instance of a workflow).
"""
import asyncio
import base64
import time
from typing import Any, Dict, List, Optional

import httpx

from ..core.config import settings

CATALOG_TTL_SECONDS = 600
# DocumentIA keeps the GPU on for minutes after the last use: asking more often
# than this does nothing but add calls.
PREPARE_EVERY_SECONDS = 120


class DocumentIAError(Exception):
    """A failed call. `retryable` tells a passing failure (network, 5xx, 429)
    from one that will fail again (configuration, a file DocumentIA refuses)."""

    def __init__(self, message: str, retryable: bool = False, status: Optional[int] = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


_token: Dict[str, Any] = {"value": None, "expires": 0.0}
_catalog: Dict[str, Any] = {"types": None, "expires": 0.0}
_prepared: Dict[str, float] = {"at": float("-inf")}
_lock = asyncio.Lock()


def configured() -> bool:
    return bool(settings.DOCUMENTIA_URL and settings.DOCUMENTIA_TOKEN_URL
                and settings.DOCUMENTIA_CLIENT_ID and settings.DOCUMENTIA_CLIENT_SECRET)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(settings.DOCUMENTIA_HTTP_TIMEOUT, connect=10.0))


def _raise_for(response: httpx.Response, what: str) -> None:
    if response.status_code < 400:
        return
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    retryable = response.status_code >= 500 or response.status_code == 429
    raise DocumentIAError(f"{what}: {detail or response.status_code}", retryable=retryable,
                          status=response.status_code)


async def _bearer(client: httpx.AsyncClient) -> str:
    async with _lock:
        if _token["value"] and time.monotonic() < _token["expires"] - 30:
            return _token["value"]
        try:
            response = await client.post(settings.DOCUMENTIA_TOKEN_URL, data={
                "grant_type": "client_credentials",
                "client_id": settings.DOCUMENTIA_CLIENT_ID,
                "client_secret": settings.DOCUMENTIA_CLIENT_SECRET,
            })
        except httpx.HTTPError as exc:
            raise DocumentIAError(f"DocumentIA token: {exc}", retryable=True) from exc
        _raise_for(response, "DocumentIA token")
        body = response.json()
        _token["value"] = body["access_token"]
        _token["expires"] = time.monotonic() + int(body.get("expires_in", 60))
        return _token["value"]


async def _call(method: str, path: str, what: str, **kwargs) -> Any:
    if not configured():
        raise DocumentIAError("DocumentIA is not configured")
    async with _client() as client:
        headers = {"Authorization": f"Bearer {await _bearer(client)}"}
        try:
            response = await client.request(method, f"{settings.DOCUMENTIA_URL.rstrip('/')}{path}",
                                            headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise DocumentIAError(f"{what}: {exc}", retryable=True) from exc
        if response.status_code == 401:   # token revoked or rotated: one more try with a new one
            _token["value"] = None
            headers = {"Authorization": f"Bearer {await _bearer(client)}"}
            response = await client.request(method, f"{settings.DOCUMENTIA_URL.rstrip('/')}{path}",
                                            headers=headers, **kwargs)
        _raise_for(response, what)
        return response.json()


async def document_types() -> Dict[str, Dict[str, Any]]:
    """The catalog, by type: `{type: {tipo, nombre, version, descripcion, max_paginas, esquema}}`."""
    if _catalog["types"] is not None and time.monotonic() < _catalog["expires"]:
        return _catalog["types"]
    types = {t["tipo"]: t for t in await _call("GET", "/v1/documentos", "DocumentIA catalog")}
    _catalog.update(types=types, expires=time.monotonic() + CATALOG_TTL_SECONDS)
    return types


async def submit(document_type: str, files: List[Dict[str, Any]]) -> str:
    """Queue the reading of a document; `files` are `{content: bytes, filename, content_type}`.
    Returns the job id."""
    body = {"archivos": [{
        "base64": base64.b64encode(f["content"]).decode("ascii"),
        "nombre": f.get("filename"),
        "content_type": f.get("content_type"),
    } for f in files]}
    answer = await _call("POST", f"/v1/documentos/{document_type}", "DocumentIA read", json=body)
    return answer["job_id"]


async def job(job_id: str) -> Dict[str, Any]:
    """`{job_id, estado: encolado|corriendo|hecho|error, resultado?, error?}`."""
    return await _call("GET", f"/v1/jobs/{job_id}", "DocumentIA job")


async def prepare_gpu() -> None:
    """Ask DocumentIA to turn its GPU on before the document arrives.

    The GPU starts on demand and takes minutes; asked while the person is still
    filling in the form, it is ready when the file is sent. Best effort: a
    failure only means the reading waits for the GPU as before."""
    if not configured() or time.monotonic() - _prepared["at"] < PREPARE_EVERY_SECONDS:
        return
    _prepared["at"] = time.monotonic()
    try:
        await _call("POST", "/v1/gpu/preparar", "DocumentIA GPU")
    except DocumentIAError:
        _prepared["at"] = float("-inf")


def reset_cache() -> None:
    """For tests and after a configuration change."""
    _token.update(value=None, expires=0.0)
    _catalog.update(types=None, expires=0.0)
    _prepared.update(at=float("-inf"))
