"""
El documento a firmar no puede contener al documento a firmar.

`SignerOperator._prepare_signable_data()` construía el payload con
`context.copy()` — el contexto entero. El ejecutor, en la rama `WAITING`, mezcla
la salida del operador de vuelta en el contexto, así que el `signable_data`
resultante quedaba dentro del contexto y la siguiente ejecución lo copiaba otra
vez dentro del nuevo. Un nivel de anidamiento por re-ejecución.

Como el operador espera event-driven, lo despierta el safety net cada 300s: un
nivel cada cinco minutos. Mongo topa el anidamiento en 180, de modo que un
trámite que llegaba a la firma y no se firmaba en unas quince horas ya no podía
guardarse nunca más. En dev había siete instancias en 179–180 niveles, todas de
`admin_validacion_conapesca` y todas muertas en `sign_document`.

Aparte, el `timestamp` se regeneraba en cada llamada, así que el `data_hash`
—que debería ser el ancla de integridad de la firma— cambiaba cada cinco
minutos. Se sella una vez, al crear la solicitud de firma.

Son pruebas puras: no tocan Mongo ni Keycloak.
"""

import os
import sys

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.workflows.operators.signer_operator import SignerOperator


def _profundidad(o, nivel=0):
    if isinstance(o, dict):
        return max([_profundidad(v, nivel + 1) for v in o.values()] or [nivel])
    if isinstance(o, list):
        return max([_profundidad(v, nivel + 1) for v in o] or [nivel])
    return nivel


def _operador():
    return SignerOperator(
        task_id="sign_document",
        context_fields_to_sign=["validation_result"],
    )


def _contexto():
    return {
        "customer_name": "DOLORES GARCIA CASILLAS",
        "validation_result": {"decision": "approved", "by": "revisor"},
        "documents": [{"filename": "estudio.pdf", "s3_key": "a/b/c.pdf"}],
    }


def _reejecutar(operador, contexto, veces):
    """
    Simula `veces` re-ejecuciones del paso: el ejecutor mezcla la salida del
    operador de vuelta en el contexto en cada vuelta (rama WAITING).
    """
    for _ in range(veces):
        contexto["signable_data"] = operador._prepare_signable_data(contexto)
    return contexto


def test_el_payload_no_se_anida_al_reejecutarse():
    operador = _operador()
    contexto = _contexto()

    _reejecutar(operador, contexto, 1)
    profundidad_inicial = _profundidad(contexto)

    _reejecutar(operador, contexto, 30)

    assert _profundidad(contexto) == profundidad_inicial


def test_el_payload_no_se_contiene_a_si_mismo():
    operador = _operador()
    contexto = _reejecutar(_operador(), _contexto(), 2)

    assert "signable_data" not in contexto["signable_data"]


def test_el_hash_es_estable_si_el_contexto_no_cambia():
    """
    El hash es lo que ancla la firma. Si cambia en cada sondeo, el revisor firma
    algo que el backend ya sustituyó.
    """
    operador = _operador()
    contexto = _contexto()

    primero = operador._prepare_signable_data(contexto)
    contexto["_signature_pending_digital_signature"] = {
        "created_at": primero["timestamp"],
    }
    segundo = operador._prepare_signable_data(contexto)

    assert segundo["data_hash"] == primero["data_hash"]


def test_el_hash_cambia_si_el_contexto_cambia():
    """Estabilizar el hash no puede volverlo ciego a un cambio real."""
    operador = _operador()
    contexto = _contexto()

    primero = operador._prepare_signable_data(contexto)
    contexto["_signature_pending_digital_signature"] = {
        "created_at": primero["timestamp"],
    }
    contexto["validation_result"]["decision"] = "rejected"
    segundo = operador._prepare_signable_data(contexto)

    assert segundo["data_hash"] != primero["data_hash"]


def test_el_registro_de_firma_pendiente_sella_el_mismo_timestamp_del_payload():
    """
    El sellado solo sirve si el registro pendiente guarda exactamente el
    timestamp que viajó en el payload. Con un `utcnow()` propio, el hash cambiaba
    una vez entre el primer envío y el primer sondeo — la misma fuga de
    integridad, más pequeña.
    """
    operador = _operador()
    contexto = _contexto()

    payload = operador._prepare_signable_data(contexto)
    registro = operador._build_pending_signature_record(payload)

    assert registro["created_at"] == payload["timestamp"]
    assert registro["data_hash"] == payload["data_hash"]


def test_el_hash_no_se_mueve_en_la_secuencia_real():
    """Primer envío y sondeos posteriores deben anclar el mismo hash."""
    operador = _operador()
    contexto = _contexto()

    primero = operador._prepare_signable_data(contexto)
    clave = "_signature_pending_digital_signature"
    contexto[clave] = operador._build_pending_signature_record(primero)
    contexto["signable_data"] = primero

    for _ in range(5):
        siguiente = operador._prepare_signable_data(contexto)
        contexto["signable_data"] = siguiente
        assert siguiente["data_hash"] == primero["data_hash"]


def test_se_siguen_excluyendo_los_campos_internos_y_sensibles():
    operador = _operador()
    contexto = _contexto()
    contexto["_signature_pending_digital_signature"] = {"status": "pending"}
    contexto["kc_token"] = "no-debe-firmarse"

    payload = operador._prepare_signable_data(contexto)

    assert "kc_token" not in payload
    assert not [k for k in payload if k.startswith("_")]


def test_el_contenido_a_firmar_se_conserva():
    """El arreglo no puede vaciar el documento que el revisor tiene que ver."""
    operador = _operador()

    payload = operador._prepare_signable_data(_contexto())

    assert payload["validation_result"] == {"decision": "approved", "by": "revisor"}
    assert payload["customer_name"] == "DOLORES GARCIA CASILLAS"
