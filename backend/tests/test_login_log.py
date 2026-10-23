"""
Cada inicio de sesión se registra también en ``<LOGIN_LOG_DIR>/<user_id>.log``.

Lo que se loguea dentro de ``login_session`` (por cualquier logger, y también en
las tareas de asyncio creadas dentro, como la consulta al RNPA) va al archivo
del ciudadano; lo de fuera no.

    docker exec backend-conapesca python -m pytest /app/tests/test_login_log.py -q
"""

import asyncio
import logging
import os
import sys
import tempfile

from beanie import PydanticObjectId

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.api.endpoints.public_auth import _log_inicio_de_sesion  # noqa: E402
from app.auth.auth_callbacks import (  # noqa: E402
    clear_post_auth_callbacks,
    register_post_auth_callback,
    run_post_auth_callbacks,
)
from app.core.database import connect_to_mongo  # noqa: E402
from app.core.login_log import LoginLogHandler, install_login_log, login_session  # noqa: E402
from app.models.customer import Customer, CustomerStatus  # noqa: E402

logger = logging.getLogger("prueba.login_log")


def _con_handler(prueba):
    raiz = logging.getLogger()
    nivel = raiz.level
    raiz.setLevel(logging.INFO)
    with tempfile.TemporaryDirectory() as directorio:
        handler = install_login_log(directorio)
        try:
            prueba(directorio, handler)
        finally:
            raiz.removeHandler(handler)
            raiz.setLevel(nivel)


def _leer(directorio, user_id):
    ruta = os.path.join(directorio, f"{user_id}.log")
    if not os.path.exists(ruta):
        return ""
    with open(ruta, encoding="utf-8") as archivo:
        return archivo.read()


def test_sin_directorio_no_se_instala():
    assert install_login_log(None) is None
    assert install_login_log("") is None


def test_instalar_dos_veces_no_duplica():
    def prueba(directorio, handler):
        assert install_login_log(directorio) is handler
        assert sum(isinstance(h, LoginLogHandler) for h in logging.getLogger().handlers) == 1

    _con_handler(prueba)


def test_solo_lo_del_inicio_de_sesion_va_al_archivo_del_ciudadano():
    def prueba(directorio, _):
        logger.info("antes")
        with login_session("u1"):
            logger.info("durante u1")
        with login_session("u2"):
            logging.getLogger("otro.modulo").warning("durante u2")
        logger.info("después")
        assert sorted(os.listdir(directorio)) == ["u1.log", "u2.log"]
        assert "durante u1" in _leer(directorio, "u1")
        assert "antes" not in _leer(directorio, "u1") and "después" not in _leer(directorio, "u1")
        assert "WARNING otro.modulo: durante u2" in _leer(directorio, "u2")
        assert "durante u1" not in _leer(directorio, "u2")

    _con_handler(prueba)


def test_la_tarea_en_segundo_plano_sigue_en_el_archivo():
    def prueba(directorio, _):
        async def principal():
            soltar = asyncio.Event()

            async def en_segundo_plano():
                await soltar.wait()
                logger.info("tarea terminó")

            with login_session("u1"):
                tarea = asyncio.create_task(en_segundo_plano())
            logger.info("request terminó")
            soltar.set()
            await tarea

        asyncio.run(principal())
        contenido = _leer(directorio, "u1")
        assert "tarea terminó" in contenido
        assert "request terminó" not in contenido

    _con_handler(prueba)


def test_el_id_no_puede_salir_del_directorio():
    def prueba(directorio, _):
        with login_session("../fuera"):
            logger.info("x")
        assert os.listdir(directorio) == ["___fuera.log"]

    _con_handler(prueba)


def test_inicio_de_sesion_con_claims_y_callbacks():
    payload = {
        "sub": "kc-prueba-login-log", "sid": "s1", "curp": "VIRP841005MDFLDL02",
        "given_name": "PAOLA", "primer_apellido": "VILLARREAL",
        "personas_morales": '[{"rfc": "PAI241215MB0", "razonSocial": "NUEVO HORIZONTE"}]',
    }

    async def callback(c, p, tenant_id):
        logging.getLogger("workflows.hooks.prueba").info("callback vio %s", c.curp)

    async def inicio_de_sesion():
        await connect_to_mongo()
        customer = Customer(
            email="prueba-login-log@example.com", full_name="Prueba", phone="", document_number="",
            password_hash="", status=CustomerStatus.ACTIVE, keycloak_id="kc-prueba-login-log",
            curp="VIRP841005MDFLDL02", tipo_persona="fisica",
            metadata={"llavemx": {"personas_morales": [{"rfc": "PAI241215MB0"}]}},
        )
        customer.id = PydanticObjectId("64b000000000000000000001")
        with login_session(str(customer.id)):
            _log_inicio_de_sesion(customer, payload)
            await run_post_auth_callbacks(customer, payload, "conapesca")
        return str(customer.id)

    def prueba(directorio, _):
        clear_post_auth_callbacks()
        register_post_auth_callback(callback)
        try:
            user_id = asyncio.run(inicio_de_sesion())
        finally:
            clear_post_auth_callbacks()
        contenido = _leer(directorio, user_id)
        assert "sesión sid:s1" in contenido
        assert "claim given_name: PAOLA" in contenido
        assert "claim primer_apellido: VILLARREAL" in contenido
        assert "claim segundo_apellido: (ausente)" in contenido
        assert "claim personas_morales: 1 persona(s) moral(es)" in contenido
        assert "rfc PAI241215MB0, razón social NUEVO HORIZONTE" in contenido
        assert "perfil guardado: curp VIRP841005MDFLDL02" in contenido
        assert "test_inicio_de_sesion_con_claims_y_callbacks.<locals>.callback: inicia" in contenido
        assert "callback vio VIRP841005MDFLDL02" in contenido
        assert "test_inicio_de_sesion_con_claims_y_callbacks.<locals>.callback: termina" in contenido

    _con_handler(prueba)
