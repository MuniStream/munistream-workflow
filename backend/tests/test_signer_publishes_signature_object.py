"""El SignerOperator debe publicar la firma como objeto, no solo en piezas sueltas.

`SignedPDFVisualizer` —que es el que arma los documentos oficiales firmados—
lee `entity.data["signature"]` y espera un dict con `signature`, `certificate` y
`algorithm`; si falta alguno marca la entidad como inválida y `generate_pdf`
revienta, y si en su lugar le llega la cadena base64 suelta, `validate_entity`
truena con `'str' object has no attribute 'get'`.

El operador solo dejaba `sign_document_signature_valid`, `_signer`, `_signed_at`,
`_algorithm` y `_signature_chain`: piezas sueltas que ningún trámite podía
componer, porque `data_mapping` mapea claves planas y no construye dicts. El
resultado es que las credenciales firmadas se imprimían como "Documento oficial".

Pruebas puras: no tocan Mongo ni Keycloak.
"""

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.workflows.operators.signer_operator import SignerOperator

CADENA = "Be60Gu1C3gA4mmQFQKLL2hNEHdXxwE7AG3r1ezU/Colw1ZFzfw2UFV9IuzKQDL63"
CERT = "-----BEGIN CERTIFICATE-----\nMIIGQTCCBCmg\n-----END CERTIFICATE-----"


def _contexto():
    return {
        "instance_id": "inst-1",
        "digital_signature": CADENA,
        "digital_signature_certificate": CERT,
        "certificate_info": {"subject": "PAOLA VILLARREAL RODRIGUEZ", "serialNumber": "0001"},
        "algorithm": "RSA-SHA256",
        "timestamp": "2026-09-09T14:38:34.880Z",
    }


def _firma_publicada(context):
    op = SignerOperator(task_id="sign_document", context_fields_to_sign=["instance_id"])
    return op.build_signature_object(context, context.get("digital_signature"))


def test_publica_los_campos_que_exige_el_visualizador():
    firma = _firma_publicada(_contexto())
    faltantes = [c for c in ("signature", "certificate", "algorithm") if c not in firma]
    assert not faltantes, f"faltan campos obligatorios: {faltantes}"
    assert firma["signature"] == CADENA
    assert firma["certificate"] == CERT
    assert firma["algorithm"] == "RSA-SHA256"


def test_incluye_la_informacion_del_certificado():
    firma = _firma_publicada(_contexto())
    assert firma.get("certificate_info", {}).get("subject") == "PAOLA VILLARREAL RODRIGUEZ"


def test_es_un_dict_y_no_la_cadena_suelta():
    """`validate_entity` hace `signature_data.get(...)`: un str lo hace tronar."""
    firma = _firma_publicada(_contexto())
    assert isinstance(firma, dict)
    assert hasattr(firma, "get")


def test_sin_certificado_no_inventa_uno():
    ctx = _contexto()
    ctx.pop("digital_signature_certificate")
    ctx.pop("certificate_info")
    firma = _firma_publicada(ctx)
    assert firma["signature"] == CADENA
    assert firma["certificate"] is None
    assert firma["algorithm"] == "RSA-SHA256"


def test_el_algoritmo_cae_al_estandar_cuando_no_viene():
    ctx = _contexto()
    ctx.pop("algorithm")
    assert _firma_publicada(ctx)["algorithm"] == "RSA-SHA256"


def test_la_salida_del_operador_incluye_la_firma():
    """La clave publicada es `<task_id>_signature`, mapeable a la entidad."""
    import inspect

    fuente = inspect.getsource(SignerOperator.execute_async)
    assert '_signature"] = self.build_signature_object' in fuente or \
           "_signature'] = self.build_signature_object" in fuente, \
        "execute_async debe publicar <task_id>_signature con el objeto de firma"
