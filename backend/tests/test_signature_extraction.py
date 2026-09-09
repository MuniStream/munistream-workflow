"""Las plantillas leen las firmas de la clave que el operador declara.

`SignerOperator.signatures_key` (default `firmas`) es el único lugar. No hay
reconstrucción a partir de claves sueltas: eso acoplaba la presentación al
nombre de la tarea y obligaba a heurísticas para no confundir cualquier
`<paso>_submitted_at` con una firma.

Pruebas puras: no tocan Mongo ni Keycloak.
"""

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.services.signature_extraction import extract_signatures, format_signature_chain

CADENA = "Be60Gu1C3gA4mmQFQKLL2hNEHdXxwE7AG3r1ezU/Colw1ZFzfw2UFV9IuzKQDL63fVJfNcfi"


def _firma(task_id, **extra):
    base = {
        "task_id": task_id,
        "signer": "PAOLA VILLARREAL RODRIGUEZ",
        "signed_at": "2026-09-09T14:38:35",
        "algorithm": "RSA-SHA256",
        "signature_valid": True,
        "signature_chain": CADENA,
    }
    base.update(extra)
    return base


def test_lee_la_lista_de_la_clave():
    firmas = extract_signatures({"firmas": [_firma("firma_validacion")]})
    assert [f["task_id"] for f in firmas] == ["firma_validacion"]
    assert firmas[0]["signer"] == "PAOLA VILLARREAL RODRIGUEZ"


def test_la_clave_es_configurable():
    assert extract_signatures({"signatures": [_firma("t")]}, key="signatures")[0]["task_id"] == "t"


def test_ordena_por_fecha_de_firma():
    data = {"firmas": [_firma("b", signed_at="2026-09-09T12:00:00"),
                       _firma("a", signed_at="2026-09-09T10:00:00")]}
    assert [f["task_id"] for f in extract_signatures(data)] == ["a", "b"]


def test_prepara_la_cadena_para_imprimir():
    grupos = extract_signatures({"firmas": [_firma("t")]})[0]["signature_chain_display"].split(" ")
    assert all(len(g) <= 32 for g in grupos)
    assert "".join(grupos) == CADENA


def test_no_reconstruye_desde_claves_sueltas():
    """Los documentos emitidos antes de `signatures_key` no muestran firma."""
    legacy = {"sign_document_signer": "X", "sign_document_signature_chain": CADENA,
              "sign_document_algorithm": "RSA-SHA256"}
    assert extract_signatures(legacy) == []


def test_no_devuelve_basura():
    assert extract_signatures({}) == []
    assert extract_signatures(None) == []
    assert extract_signatures({"firmas": []}) == []
    assert extract_signatures({"firmas": "no es una lista"}) == []
    assert extract_signatures({"firmas": ["ni esto"]}) == []


def test_format_signature_chain_limpia_saltos():
    assert format_signature_chain("AB\nCD EF", group_size=2) == "AB CD EF"


def test_disponible_como_filtro_en_las_plantillas():
    from app.services.pdf_generation.template_engine import TemplateEngine

    env = TemplateEngine().env
    assert "firmas" in env.filters and "cadena_firma" in env.filters
    salida = env.from_string(
        "{% for f in data|firmas %}{{ f.signer }}|{{ f.signature_chain_display }}{% endfor %}"
    ).render(data={"firmas": [_firma("t")]})
    assert "PAOLA VILLARREAL RODRIGUEZ" in salida and CADENA[:32] in salida
