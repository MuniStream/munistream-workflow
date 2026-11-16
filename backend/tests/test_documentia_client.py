"""Client of DocumentIA: client-credentials token, catalog cache and errors."""
import asyncio
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.services import documentia
from app.services.documentia import DocumentIAError


@pytest.fixture
def server(monkeypatch):
    calls = []
    answers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": f"t{len(calls)}", "expires_in": 300})
        status, body = answers.get((request.method, request.url.path), (404, {"detail": "no_existe"}))
        if callable(body):
            return body(request)
        return httpx.Response(status, json=body)

    for name, value in (("DOCUMENTIA_URL", "http://docia.test"),
                        ("DOCUMENTIA_TOKEN_URL", "http://kc.test/realms/r/protocol/openid-connect/token"),
                        ("DOCUMENTIA_CLIENT_ID", "munistream-pruebas"), ("DOCUMENTIA_CLIENT_SECRET", "x")):
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(documentia, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(documentia, "_lock", asyncio.Lock())
    documentia.reset_cache()
    yield calls, answers
    documentia.reset_cache()


def run(coro):
    return asyncio.run(coro)


def test_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "DOCUMENTIA_URL", None)
    assert documentia.configured() is False
    with pytest.raises(DocumentIAError):
        run(documentia.job("x"))


def test_submit_and_job_share_the_token(server):
    calls, answers = server
    answers[("POST", "/v1/documentos/curp")] = (202, {"job_id": "j1"})
    answers[("GET", "/v1/jobs/j1")] = (200, {"job_id": "j1", "estado": "hecho"})

    async def flow():
        job_id = await documentia.submit("curp", [{"content": b"abc", "filename": "c.pdf",
                                                  "content_type": "application/pdf"}])
        return job_id, await documentia.job(job_id)

    job_id, job = run(flow())
    assert job_id == "j1" and job["estado"] == "hecho"
    token_calls = [c for c in calls if c.url.path.endswith("/token")]
    assert len(token_calls) == 1 and b"grant_type=client_credentials" in token_calls[0].content
    post = next(c for c in calls if c.method == "POST" and "documentos" in c.url.path)
    assert post.headers["Authorization"] == "Bearer t1"
    assert b'"base64": "YWJj"' in post.content or b'"base64":"YWJj"' in post.content


def test_catalog_is_cached(server):
    calls, answers = server
    answers[("GET", "/v1/documentos")] = (200, [{"tipo": "curp", "nombre": "CURP", "esquema": {}}])

    async def twice():
        return await documentia.document_types(), await documentia.document_types()

    first, second = run(twice())
    assert first == second and "curp" in first
    assert len([c for c in calls if c.url.path == "/v1/documentos"]) == 1


@pytest.mark.parametrize("status,retryable", [(503, True), (429, True), (422, False), (404, False)])
def test_errors(server, status, retryable):
    _, answers = server
    answers[("GET", "/v1/jobs/j")] = (status, {"detail": "motivo"})
    with pytest.raises(DocumentIAError) as caught:
        run(documentia.job("j"))
    assert caught.value.retryable is retryable and caught.value.status == status
    assert "motivo" in str(caught.value)


def test_rejected_token_is_renewed_once(server):
    calls, answers = server
    seen = []

    def jobs(request):
        seen.append(request.headers["Authorization"])
        return httpx.Response(401 if len(seen) == 1 else 200, json={"estado": "encolado"})

    answers[("GET", "/v1/jobs/j")] = (None, jobs)
    assert run(documentia.job("j"))["estado"] == "encolado"
    assert seen[0] != seen[1]
