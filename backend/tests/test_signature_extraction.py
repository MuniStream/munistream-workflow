"""Extracción de las firmas que deja el SignerOperator en una entidad.

El operador publica sus resultados como `<task_id>_<atributo>`. Catastro lo
aprovecha nombrando su tarea `firma_validacion`, y agrupa las claves buscando el
prefijo `firma_` (`workflows/oficios/__init__.py::_extract_firmas`). CONAPESCA
nombró la suya `sign_document`, así que ese barrido nunca encontraba nada y los
documentos se emitían sin bloque de firma.

Agrupar por prefijo además amarra la presentación al nombre de la tarea: si se
renombra, los documentos YA EMITIDOS —55 en desarrollo al escribir esto— dejan
de mostrar su firma, porque en su `data` quedó el nombre viejo. Por eso aquí se
detecta por el CONJUNTO DE ATRIBUTOS y no por el nombre.

Pruebas puras: no tocan Mongo ni Keycloak.
"""

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.services.signature_extraction import extract_signatures, format_signature_chain

CADENA = "Be60Gu1C3gA4mmQFQKLL2hNEHdXxwE7AG3r1ezU/Colw1ZFzfw2UFV9IuzKQDL63fVJfNcfi"


def _firma(prefijo, **extra):
    base = {
        f"{prefijo}_signer": "PAOLA VILLARREAL RODRIGUEZ",
        f"{prefijo}_signed_at": "2026-09-09T14:38:35",
        f"{prefijo}_algorithm": "RSA-SHA256",
        f"{prefijo}_signature_valid": True,
        f"{prefijo}_signature_chain": CADENA,
    }
    base.update({f"{prefijo}_{k}": v for k, v in extra.items()})
    return base


def test_encuentra_la_firma_de_catastro():
    firmas = extract_signatures(_firma("firma_validacion"))
    assert len(firmas) == 1
    assert firmas[0]["task_id"] == "firma_validacion"
    assert firmas[0]["signer"] == "PAOLA VILLARREAL RODRIGUEZ"


def test_encuentra_la_firma_de_conapesca():
    """`sign_document` no lleva el prefijo `firma_` y aun así debe detectarse."""
    firmas = extract_signatures(_firma("sign_document"))
    assert len(firmas) == 1
    assert firmas[0]["task_id"] == "sign_document"
    assert firmas[0]["algorithm"] == "RSA-SHA256"


def test_documentos_ya_emitidos_conservan_su_firma():
    """Renombrar la tarea no puede borrar la firma de lo ya emitido."""
    data = {**_firma("sign_document"), **_firma("firma_validacion")}
    firmas = extract_signatures(data)
    assert {f["task_id"] for f in firmas} == {"sign_document", "firma_validacion"}


def test_ordena_por_fecha_de_firma():
    data = {
        **_firma("firma_b", signed_at="2026-09-09T12:00:00"),
        **_firma("firma_a", signed_at="2026-09-09T10:00:00"),
    }
    assert [f["task_id"] for f in extract_signatures(data)] == ["firma_a", "firma_b"]


def test_ignora_claves_que_no_son_firmas():
    data = {
        "customer_name": "X",
        "capture_selfie_validated": True,
        "upload_selfie_s3_result": {"bucket": "b"},
        "validation_result": {"approved": True},
    }
    assert extract_signatures(data) == []


def test_exige_evidencia_real_de_firma():
    """Un paso con `_signed_at` pero sin firmante ni cadena no es una firma."""
    assert extract_signatures({"confirmacion_signed_at": "2026-09-09T10:00:00"}) == []


def test_prepara_la_cadena_para_imprimir():
    firmas = extract_signatures(_firma("firma_validacion"))
    grupos = firmas[0]["signature_chain_display"].split(" ")
    assert all(len(g) <= 32 for g in grupos)
    assert "".join(grupos) == CADENA


def test_format_signature_chain_limpia_saltos():
    assert format_signature_chain("AB\nCD EF", group_size=2) == "AB CD EF"


def test_sin_firmas_devuelve_lista_vacia():
    assert extract_signatures({}) == []
    assert extract_signatures(None) == []


def test_disponible_como_filtro_en_las_plantillas():
    """Las plantillas de cualquier tenant deben poder pedir las firmas sin
    copiarse el extractor, que es justo lo que hoy hace catastro."""
    from app.services.pdf_generation.template_engine import TemplateEngine

    env = TemplateEngine().env
    assert "firmas" in env.filters
    assert "cadena_firma" in env.filters

    plantilla = env.from_string(
        "{% for f in data|firmas %}{{ f.signer }}|{{ f.signature_chain_display }}{% endfor %}"
    )
    salida = plantilla.render(data=_firma("sign_document"))
    assert "PAOLA VILLARREAL RODRIGUEZ" in salida
    assert CADENA[:32] in salida


def test_prefiere_la_clave_explicita():
    """Cuando el operador ya dejó la lista, se usa tal cual: sin barrido ni
    heurística. El barrido es solo compatibilidad con lo ya emitido."""
    data = {
        "firmas": [{"task_id": "firma_validacion", "signer": "QUIEN FIRMÓ",
                    "signature_chain": CADENA, "algorithm": "RSA-SHA256"}],
        # Claves sueltas de otra tarea: NO deben mezclarse ni duplicar.
        **_firma("sign_document"),
    }
    firmas = extract_signatures(data)
    assert [f["signer"] for f in firmas] == ["QUIEN FIRMÓ"]


def test_la_clave_explicita_tambien_formatea_la_cadena():
    data = {"firmas": [{"task_id": "t", "signer": "X", "signature_chain": CADENA}]}
    grupos = extract_signatures(data)[0]["signature_chain_display"].split(" ")
    assert "".join(grupos) == CADENA


def test_cae_al_barrido_si_no_hay_clave_explicita():
    """Los documentos emitidos antes de `signatures_key` conservan su firma."""
    firmas = extract_signatures(_firma("sign_document"))
    assert len(firmas) == 1 and firmas[0]["task_id"] == "sign_document"


def test_la_clave_es_configurable_en_el_filtro():
    data = {"signatures": [{"task_id": "t", "signer": "Y", "signature_chain": CADENA}]}
    assert extract_signatures(data, key="signatures")[0]["signer"] == "Y"
