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


def test_acumula_sobre_las_firmas_del_padre():
    """Un trámite con dos validaciones admin firma en DOS workflows hijo.

    El hijo recibe el contexto del padre anidado en `_parent_context`, y `firmas`
    no viaja entre los campos planos que pasa el WorkflowStartOperator. Sin mirar
    ahí, el segundo firmante arrancaría con la lista vacía y al volver al padre
    PISARÍA la firma del primero: el oficio saldría con una sola firma.
    """
    primera = {"task_id": "firma_validacion", "signer": "JURÍDICO", "signature": "AAA"}
    ctx = _ctx(_parent_context={"firmas": [primera]})
    firmas = _op(task_id="firma_dictamen_tecnico").collect_signatures(ctx, CADENA)
    assert [f["task_id"] for f in firmas] == ["firma_validacion", "firma_dictamen_tecnico"]


def test_la_lista_propia_gana_sobre_la_del_padre():
    """Si el hijo ya tiene la lista al alcance, esa manda: es la más reciente."""
    padre = {"task_id": "firma_vieja", "signer": "VIEJO", "signature": "AAA"}
    propia = {"task_id": "firma_validacion", "signer": "ACTUAL", "signature": "BBB"}
    ctx = _ctx(firmas=[propia], _parent_context={"firmas": [padre]})
    firmas = _op(task_id="firma_dictamen_tecnico").collect_signatures(ctx, CADENA)
    assert [f["task_id"] for f in firmas] == ["firma_validacion", "firma_dictamen_tecnico"]


def test_parent_context_malformado_no_rompe():
    for basura in ("no es dict", None, {"firmas": "no es lista"}, {}):
        firmas = _op().collect_signatures(_ctx(_parent_context=basura), CADENA)
        assert [f["task_id"] for f in firmas] == ["firma_validacion"]
