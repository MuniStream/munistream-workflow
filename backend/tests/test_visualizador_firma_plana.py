"""El visualizador firmado acepta las DOS formas del bloque de firma.

`entity.data["signature"]` circula anidado (un dict con la firma y sus
metadatos) y también aplanado (la firma es una cadena base64 y `algorithm`,
`certificate_info`, `signer`… son hermanos suyos en la raíz de `data`).

`validate_entity` asumía siempre la primera y con la segunda moría en
`signature_data.get("certificate_info", {})` con `'str' object has no attribute
'get'`. El endpoint devolvía 500 y el revisor veía, en la vista previa del
documento de la entidad, un escueto "Error al cargar el HTML del documento".

Pruebas puras: no tocan Mongo ni Keycloak.
"""

import asyncio
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from types import SimpleNamespace

from app.services.visualizers.signed_pdf_visualizer import SignedPDFVisualizer

CADENA = "PDd6VCMD0MGHcfk8sn6Hc+Fkoxy5rZlyvyR9X7HS1En4QJV09Ct3VYeoYvnA"

METADATOS = {
    "algorithm": "RSA-SHA256",
    "certificate": "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----",
    "certificate_info": {"subject": "E2E Test CONAPESCA"},
    "cert_subject": "E2E Test CONAPESCA",
    "signer": "E2E Test CONAPESCA",
    "signature_valid": True,
    "timestamp": "2026-09-30T00:33:40.726624",
}


def _entidad(data):
    # Un doble y no el modelo de Beanie: instanciarlo exige inicializar Mongo, y
    # la validación solo mira `entity_id`, `name` y `data`.
    return SimpleNamespace(
        entity_id="pescador_rnpa_prueba",
        entity_type="pescador_rnpa",
        owner_user_id="usuario-de-prueba",
        name="Pescador rnpa",
        data=data,
    )


def _validar(entity):
    return asyncio.run(SignedPDFVisualizer(config={}).validate_entity(entity))


def test_firma_plana_no_revienta():
    """La forma que emite el SignerOperator hacia la entidad."""
    resultado = _validar(_entidad({"signature": CADENA, **METADATOS, "numero_rnpa": "RNPA-1"}))

    assert resultado["valid"] is True
    assert resultado["errors"] == []
    # Los metadatos hermanos se reconocen: no se reporta como firma incompleta.
    assert not any("certificate" in str(a).lower() for a in resultado["warnings"])


def test_firma_anidada_sigue_funcionando():
    resultado = _validar(_entidad({"signature": {"signature": CADENA, **METADATOS}}))

    assert resultado["valid"] is True
    assert resultado["errors"] == []


def test_sin_firma_avisa_pero_no_falla():
    resultado = _validar(_entidad({"numero_rnpa": "RNPA-1"}))

    assert resultado["valid"] is True
    assert any("signature" in str(a).lower() for a in resultado["warnings"])
