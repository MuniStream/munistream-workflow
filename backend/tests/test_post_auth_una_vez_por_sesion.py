"""
Los callbacks post-auth corren una vez por sesión de Keycloak, no en cada request.

`run_post_auth_callbacks` se ejecutaba con `await` dentro de
`get_current_customer`, así que "post-auth" significaba en realidad "en cada
petición autenticada del portal": un callback que consultara un servicio externo
lo golpeaba en cada clic. Ahora la request que reclama la sesión (`sid` del
token) es la única que los ejecuta.

Corre contra la Mongo del contenedor del backend:

    docker exec backend-conapesca python -m pytest /app/tests/test_post_auth_una_vez_por_sesion.py -q
"""

import asyncio
import os
import sys

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.auth.auth_callbacks import (  # noqa: E402
    POST_AUTH_SESSION_KEY,
    claim_post_auth_session,
    post_auth_session_key,
)
from app.core.database import connect_to_mongo  # noqa: E402
from app.models.customer import Customer, CustomerStatus  # noqa: E402


async def _con_customer(prueba):
    await connect_to_mongo()
    customer = Customer(
        email="prueba-post-auth-sesion@example.com", full_name="Prueba sesión",
        phone="", document_number="", password_hash="", status=CustomerStatus.ACTIVE,
        keycloak_id="kc-prueba-post-auth-sesion",
    )
    await customer.insert()
    try:
        await prueba(customer)
    finally:
        await customer.delete()


def test_la_clave_sale_del_sid_y_cae_a_session_state_y_auth_time():
    assert post_auth_session_key({"sid": "s1", "auth_time": 5}) == "sid:s1"
    assert post_auth_session_key({"session_state": "s2"}) == "sid:s2"
    assert post_auth_session_key({"auth_time": 1700000000}) == "auth_time:1700000000"
    assert post_auth_session_key({}) is None


def test_la_segunda_request_de_la_misma_sesion_no_los_ejecuta():
    async def prueba(customer):
        assert await claim_post_auth_session(customer, {"sid": "s1"}) is True
        # Otra request de la misma sesión carga el Customer de nuevo desde Mongo.
        otra = await Customer.get(customer.id)
        assert await claim_post_auth_session(otra, {"sid": "s1"}) is False
        # Un login nuevo trae otro sid y vuelve a ejecutarlos.
        assert await claim_post_auth_session(otra, {"sid": "s2"}) is True

    asyncio.run(_con_customer(prueba))


def test_de_varias_requests_concurrentes_del_login_solo_una_gana():
    async def prueba(customer):
        cargados = [await Customer.get(customer.id) for _ in range(5)]
        resultados = await asyncio.gather(
            *(claim_post_auth_session(c, {"sid": "s-concurrente"}) for c in cargados)
        )
        assert resultados.count(True) == 1

    asyncio.run(_con_customer(prueba))


def test_un_save_posterior_no_borra_la_marca():
    async def prueba(customer):
        await claim_post_auth_session(customer, {"sid": "s1"})
        await customer.save()  # lo que hacen los callbacks
        guardado = await Customer.get(customer.id)
        assert guardado.metadata[POST_AUTH_SESSION_KEY] == "sid:s1"

    asyncio.run(_con_customer(prueba))


def test_sin_identificador_de_sesion_conserva_el_comportamiento_anterior():
    async def prueba(customer):
        assert await claim_post_auth_session(customer, {}) is True
        assert await claim_post_auth_session(customer, {}) is True

    asyncio.run(_con_customer(prueba))
