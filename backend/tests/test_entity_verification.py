"""
El QR de verificación declara inválido todo documento que CONAPESCA emitió.

Medido en dev: **0 de 58 entidades** tienen `verified: True`, incluidas las 23 que
son documentos emitidos. Ningún workflow lo pone nunca, y `verify.py` declara
`valid: false` solo por eso. Una concesión activa y legítima responde
`{"valid": false, "validation_errors": ["Entity is not verified"]}`.

El error es de semántica. `verified` significa "una persona dio fe de este
documento", que tiene sentido para uno **subido** por el ciudadano —una INE, un
acta—. Un documento que **emitió la propia dependencia** es auténtico por
construcción: está en la base porque el sistema lo generó al completar un
trámite, y eso lo registra `created_by_workflow`. Exigirle visto bueno humano al
documento que uno mismo firmó no tiene sentido.

Y por debajo hay tres fallos más, cada uno suficiente para invalidar un QR bueno:

1. **El checksum se calculaba sobre microsegundos que Mongo trunca.** El PDF se
   genera con la entidad en memoria (microsegundos) y `verify.py` recalcula
   leyendo de Mongo (milisegundos). Los hashes difieren en 999 de cada 1000
   emisiones.
2. **El checksum incluía `status`, que es mutable.** Un QR ya impreso se
   autoinvalidaba en cuanto el documento cambiaba de estado. El QR es estático;
   el material que se hashea no puede serlo.
3. **Se ignoraba `valid_until`** —el campo canónico de vigencia— y se adivinaba
   la caducidad desde `data` con un `except:` desnudo que convertía cualquier
   formato inesperado en "sospechoso".

Más: los motivos se le mostraban al ciudadano crudos y en inglés, literalmente
*"Entity is not verified"*.

Son pruebas puras: no tocan Mongo.
"""

import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.services.entity_verification import (
    checksum_de,
    dictaminar,
    es_documento_emitido,
)

AHORA = datetime(2026, 9, 29, 12, 0, 0)


def _emitida(**kwargs):
    """Una concesión emitida por un trámite: el caso que hoy falla al 100%."""
    base = dict(
        entity_id="concesion_pesca_comercial_a1b2c3d4",
        entity_type="concesion_pesca_comercial",
        name="Concesión de pesca comercial — Doña Petra",
        status="active",
        verified=False,
        verification_date=None,
        verified_by=None,
        valid_until=datetime(2031, 3, 1),
        created_by_workflow="19dae91b-e83c-485b-a064-b9d163ea2f55",
        created_at=datetime(2026, 3, 1, 10, 30, 0),
        data={},
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _subida(**kwargs):
    """Un documento que subió el ciudadano: aquí `verified` sí significa algo."""
    return _emitida(
        entity_id="identidad_verificada_9f8e7d6c",
        entity_type="identidad_verificada",
        name="Identificación oficial",
        created_by_workflow=None,
        valid_until=None,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Qué cuenta como documento emitido
# ---------------------------------------------------------------------------

def test_lo_emitio_un_workflow_entonces_es_documento_emitido():
    assert es_documento_emitido(_emitida()) is True


def test_sin_workflow_de_origen_no_es_documento_emitido():
    assert es_documento_emitido(_subida()) is False


def test_un_workflow_de_origen_vacio_no_cuenta():
    assert es_documento_emitido(_emitida(created_by_workflow="")) is False


# ---------------------------------------------------------------------------
# El fallo reportado: un documento emitido y legítimo sale inválido
# ---------------------------------------------------------------------------

def test_un_documento_emitido_y_vigente_es_valido():
    """
    Es el punto entero del cambio. Hoy responde `valid: false` con el motivo
    "Entity is not verified", y son las 23 entidades emitidas de dev.
    """
    dictamen = dictaminar(_emitida(), ahora=AHORA)

    assert dictamen["valid"] is True
    assert dictamen["motivos"] == []


def test_un_documento_emitido_no_necesita_visto_bueno_humano():
    """La autenticidad sale de que la dependencia lo generó, no de `verified`."""
    dictamen = dictaminar(_emitida(verified=False), ahora=AHORA)

    assert dictamen["valid"] is True


def test_un_documento_subido_sin_validar_no_es_valido():
    """`verified` se conserva, pero solo donde significa algo: lo que el ciudadano sube."""
    dictamen = dictaminar(_subida(verified=False), ahora=AHORA)

    assert dictamen["valid"] is False
    assert dictamen["motivos"] == ["El documento aún no ha sido validado por la autoridad."]


def test_un_documento_subido_ya_validado_es_valido():
    dictamen = dictaminar(_subida(verified=True), ahora=AHORA)

    assert dictamen["valid"] is True


# ---------------------------------------------------------------------------
# Estado
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("estado", ["active", "vigente", "valid"])
def test_los_estados_vigentes_no_son_motivo_de_rechazo(estado):
    assert dictaminar(_emitida(status=estado), ahora=AHORA)["valid"] is True


def test_un_documento_revocado_no_es_valido():
    dictamen = dictaminar(_emitida(status="revoked"), ahora=AHORA)

    assert dictamen["valid"] is False
    assert dictamen["motivos"] == ["El documento no se encuentra vigente."]


# ---------------------------------------------------------------------------
# Vigencia: sale de `valid_until`, no de adivinar dentro de `data`
# ---------------------------------------------------------------------------

def test_la_vigencia_sale_del_campo_canonico():
    """
    `valid_until` es el campo que puebla la emisión y que una prórroga actualiza.
    Antes se ignoraba por completo y se hurgaba en `data["fecha_vencimiento"]`.
    """
    dictamen = dictaminar(_emitida(valid_until=datetime(2025, 1, 1)), ahora=AHORA)

    assert dictamen["valid"] is False
    assert dictamen["motivos"] == ["El documento venció el 01/01/2025."]


def test_una_vigencia_futura_no_estorba():
    assert dictaminar(_emitida(valid_until=AHORA + timedelta(days=1)), ahora=AHORA)["valid"] is True


def test_sin_fecha_de_vigencia_el_documento_no_vence():
    """
    Muchas entidades no tienen vigencia declarada. Ausencia de fecha no es
    vencimiento: antes un formato inesperado caía en un `except:` desnudo y se
    reportaba como "Invalid expiry date format", que el ciudadano leía como
    sospecha.
    """
    dictamen = dictaminar(_emitida(valid_until=None, data={}), ahora=AHORA)

    assert dictamen["valid"] is True
    assert dictamen["motivos"] == []


def test_una_vigencia_heredada_en_data_se_sigue_respetando():
    """Las entidades anteriores al campo solo tienen `vigencia_anos` en `data`."""
    entidad = _emitida(
        valid_until=None,
        created_at=datetime(2020, 1, 1),
        data={"vigencia_anos": 1},
    )

    assert dictaminar(entidad, ahora=AHORA)["valid"] is False


def test_una_fecha_ilegible_no_declara_sospechoso_el_documento():
    entidad = _emitida(valid_until=None, data={"fecha_vencimiento": "el año que viene"})

    dictamen = dictaminar(entidad, ahora=AHORA)

    assert dictamen["valid"] is True


# ---------------------------------------------------------------------------
# Checksum: material estable, idéntico al generar y al verificar
# ---------------------------------------------------------------------------

def test_el_checksum_no_cambia_al_truncarse_los_microsegundos():
    """
    El PDF se genera con la entidad en memoria —`created_at` con microsegundos— y
    la verificación recalcula leyendo de Mongo, que trunca a milisegundos. Es lo
    que hacía fallar 999 de cada 1000 emisiones.
    """
    en_memoria = _emitida(created_at=datetime(2026, 3, 1, 10, 30, 0, 123456))
    en_mongo = _emitida(created_at=datetime(2026, 3, 1, 10, 30, 0, 123000))

    assert checksum_de(en_memoria) == checksum_de(en_mongo)


def test_el_checksum_no_cambia_cuando_cambia_el_estado():
    """
    Un QR ya impreso no puede autoinvalidarse porque el documento pase de
    `active` a `vigente`, ni por una prórroga o una suspensión.
    """
    assert checksum_de(_emitida(status="active")) == checksum_de(_emitida(status="vigente"))


def test_el_checksum_cambia_si_cambia_el_nombre():
    """Es lo que el checksum sí debe detectar: el dato impreso alterado."""
    assert checksum_de(_emitida()) != checksum_de(_emitida(name="Otro titular"))


def test_el_checksum_cambia_si_cambia_el_folio():
    assert checksum_de(_emitida()) != checksum_de(_emitida(entity_id="concesion_pesca_comercial_ffffffff"))


def test_el_checksum_cambia_si_cambia_la_fecha_de_emision():
    otro_dia = _emitida(created_at=datetime(2026, 3, 2, 10, 30, 0))

    assert checksum_de(_emitida()) != checksum_de(otro_dia)


def test_el_checksum_es_estable_entre_llamadas():
    assert checksum_de(_emitida()) == checksum_de(_emitida())


def test_el_checksum_mide_dieciseis_caracteres():
    """Lo que cabe en el QR, y lo que ya emiten los PDFs."""
    valor = checksum_de(_emitida())

    assert len(valor) == 16
    assert valor == valor.lower()


# ---------------------------------------------------------------------------
# Checksum dentro del dictamen
# ---------------------------------------------------------------------------

def test_el_checksum_correcto_no_estorba():
    entidad = _emitida()

    dictamen = dictaminar(entidad, checksum=checksum_de(entidad), ahora=AHORA)

    assert dictamen["valid"] is True
    assert dictamen["checksum_valid"] is True


def test_un_checksum_que_no_cuadra_invalida_el_documento():
    dictamen = dictaminar(_emitida(), checksum="0000000000000000", ahora=AHORA)

    assert dictamen["valid"] is False
    assert dictamen["checksum_valid"] is False
    assert dictamen["motivos"] == [
        "Los datos impresos no coinciden con el registro oficial."
    ]


def test_el_checksum_se_compara_sin_importar_mayusculas():
    entidad = _emitida()

    dictamen = dictaminar(entidad, checksum=checksum_de(entidad).upper(), ahora=AHORA)

    assert dictamen["checksum_valid"] is True


def test_sin_checksum_no_se_reprocha_nada():
    """Quien teclea la liga a mano no trae checksum; eso no es una anomalía."""
    dictamen = dictaminar(_emitida(), checksum=None, ahora=AHORA)

    assert dictamen["valid"] is True
    assert dictamen["checksum_valid"] is True
    assert dictamen["checksum_provided"] is False


# ---------------------------------------------------------------------------
# Lo que ve quien escanea
# ---------------------------------------------------------------------------

def test_los_motivos_estan_en_espanol():
    """
    Lo que veía el ciudadano era literalmente "Entity is not verified". El QR lo
    escanea cualquiera desde la calle: es la cara pública del sistema.
    """
    motivos = dictaminar(_emitida(status="revoked", valid_until=datetime(2025, 1, 1)),
                         ahora=AHORA)["motivos"]

    assert motivos
    for motivo in motivos:
        assert motivo == motivo.strip()
        assert motivo.endswith(".")
        assert "Entity" not in motivo


def test_se_acumulan_todos_los_motivos():
    dictamen = dictaminar(
        _emitida(status="revoked", valid_until=datetime(2025, 1, 1)),
        checksum="0000000000000000",
        ahora=AHORA,
    )

    assert len(dictamen["motivos"]) == 3


def test_el_dictamen_publica_la_vigencia_que_uso():
    """Quien verifica quiere ver hasta cuándo vale, no solo un sí o un no."""
    dictamen = dictaminar(_emitida(valid_until=datetime(2031, 3, 1)), ahora=AHORA)

    assert dictamen["valid_until"] == "2031-03-01T00:00:00"


def test_el_dictamen_dice_si_el_documento_lo_emitio_la_dependencia():
    """Es la distinción que sostiene todo el cambio; que se vea en la respuesta."""
    assert dictaminar(_emitida(), ahora=AHORA)["issued_by_authority"] is True
    assert dictaminar(_subida(verified=True), ahora=AHORA)["issued_by_authority"] is False


# ---------------------------------------------------------------------------
# Folio corto: el QR del wallet apuntaba a un documento inexistente
# ---------------------------------------------------------------------------

def test_el_folio_corto_es_el_identificador_no_el_tipo():
    """
    El wallet armaba la URL como `f"{entity_type}_{entity_id[:8]}"`, pero los ids
    ya son `{entity_type}_{uuid8}`: esos 8 caracteres recortaban **el prefijo del
    tipo**, no el uuid. Para `pescador_rnpa_a1b2c3d4` producía
    `/verify/pescador_rnpa_pescador`, que no existe. El 100% de las credenciales
    añadidas a Apple o Google Wallet apuntaba a la nada, y nunca pudo funcionar.
    """
    from app.services.entity_verification import folio_corto

    assert folio_corto("pescador_rnpa_a1b2c3d4") == "a1b2c3d4"


def test_el_folio_corto_soporta_tipos_con_muchos_guiones():
    from app.services.entity_verification import folio_corto

    assert folio_corto("concesion_pesca_comercial_ffffffff") == "ffffffff"


def test_un_id_sin_guiones_es_su_propio_folio():
    from app.services.entity_verification import folio_corto

    assert folio_corto("abc12345") == "abc12345"


def test_sin_id_el_folio_queda_vacio():
    from app.services.entity_verification import folio_corto

    assert folio_corto(None) == ""
    assert folio_corto("") == ""
