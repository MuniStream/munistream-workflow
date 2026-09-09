"""El SignerOperator publica sus firmas en una clave explícita del contexto.

Antes las firmas solo existían como claves sueltas `<task_id>_<atributo>`, así
que quien quisiera imprimirlas tenía que adivinar cuáles eran. Eso acoplaba a
los consumidores al NOMBRE de la tarea: al renombrar `sign_document` a
`firma_validacion`, el `data_mapping` del trámite que mapeaba
`"sign_document_signature"` dejó de encontrar nada, en silencio.

Con `signatures_key` el operador escribe en un lugar convenido y estable. El
trámite mapea esa clave una vez y renombrar la tarea deja de romper nada.

Pruebas puras: no tocan Mongo ni Keycloak.
"""

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.workflows.operators.signer_operator import SignerOperator

CADENA = "Be60Gu1C3gA4mmQFQKLL2hNEHdXxwE7AG3r1ezU/Colw1ZFzfw2UFV9IuzKQDL63"


def _op(task_id="firma_validacion", **kw):
    return SignerOperator(task_id=task_id, context_fields_to_sign=["instance_id"], **kw)


def _ctx(**extra):
    base = {
        "instance_id": "inst-1",
        "digital_signature": CADENA,
        "digital_signature_certificate": "-----BEGIN CERTIFICATE-----\nMII\n-----END CERTIFICATE-----",
        "algorithm": "RSA-SHA256",
        "timestamp": "2026-09-09T14:38:35",
    }
    base.update(extra)
    return base


def test_la_clave_por_defecto_es_firmas():
    assert _op().signatures_key == "firmas"


def test_la_clave_es_configurable():
    assert _op(signatures_key="signatures").signatures_key == "signatures"


def test_publica_la_firma_en_la_clave():
    op = _op()
    firmas = op.collect_signatures(_ctx(), CADENA)
    assert isinstance(firmas, list) and len(firmas) == 1
    assert firmas[0]["signature"] == CADENA
    assert firmas[0]["task_id"] == "firma_validacion"


def test_acumula_en_vez_de_pisar():
    """Un flujo con varios firmantes debe apilar las firmas, no reemplazarlas."""
    previa = {"task_id": "firma_dictamen", "signer": "OTRO FUNCIONARIO", "signature": "AAA"}
    op = _op()
    firmas = op.collect_signatures(_ctx(firmas=[previa]), CADENA)
    assert [f["task_id"] for f in firmas] == ["firma_dictamen", "firma_validacion"]


def test_re_ejecutar_la_misma_tarea_no_duplica():
    """El operador se re-ejecuta al reanudar; no debe apilar su firma dos veces."""
    op = _op()
    primera = op.collect_signatures(_ctx(), CADENA)
    segunda = op.collect_signatures(_ctx(firmas=primera), CADENA)
    assert len(segunda) == 1


def test_la_firma_lleva_lo_que_el_documento_imprime():
    firma = _op().collect_signatures(_ctx(), CADENA)[0]
    for campo in ("signer", "signed_at", "algorithm", "signature_valid",
                  "signature_chain", "cert_subject", "certificate"):
        assert campo in firma, f"falta {campo}"
    assert firma["signature_chain"] == CADENA


def test_execute_publica_la_clave():
    import inspect
    fuente = inspect.getsource(SignerOperator.execute_async)
    assert "self.signatures_key" in fuente, "execute_async debe publicar la clave configurada"
