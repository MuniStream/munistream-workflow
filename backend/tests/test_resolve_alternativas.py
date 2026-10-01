"""
Alternativas en las rutas del contexto: `a.b|c.d` toma la primera que resuelva.

`name_source` ya las admitía, pero `data_mapping` no, y ahí el fallo es peor: no
produce un nombre feo, produce **campos vacíos en el documento oficial emitido**.

Medido sobre los 71 contextos reales de la base, el título de concesión mapeaba
`_selected_entities_data.embarcacion_ids.0.nombre` a `nombre_embarcacion`, y esa
ruta resuelve **1 de cada 13 veces** —el dato vive en `nombre_embarcacion`—, así
que el título salía casi siempre sin el nombre de la embarcación. Lo mismo con la
eslora (`eslora_metros` vs `eslora`) y el arqueo (`tonelaje_trb` vs
`tonelaje_bruto`).

Y hay un caso que ninguna ruta sola cubre: de 38 solicitantes, 25 traen
`nombre_completo` y 13 `razon_social` —personas físicas y morales, sin solapamiento
y sin huecos—. Cualquier campo solo deja el documento a medias para un tercio de
la gente.

Las alternativas viven en `_resolve_context_path` y no en cada consumidor, para que
la sintaxis sea una sola en todo el motor: `data_mapping`, `name_source`, filtros y
lo que venga.

Son pruebas puras: no tocan Mongo.
"""

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.workflows.operators.entity_operators import _resolve_context_path

CONTEXTO = {
    "_selected_entities_data": {
        "embarcacion_ids": [
            {"nombre_embarcacion": "AJUA", "eslora": 12.5, "tonelaje_bruto": 8, "matricula": "MZT-1"},
            {"nombre_embarcacion": "REINA II", "eslora": 9.0, "tonelaje_bruto": 5, "matricula": "MZT-2"},
        ],
        "pescador_rnpa_ids": [{"razon_social": "Pesquera del Golfo S.A.", "rfc_empresa": "PGO010101AAA"}],
    },
    "vacio": "",
    "lista_vacia": [],
}


# ---------------------------------------------------------------------------
# Lo que ya funcionaba y no se puede romper
# ---------------------------------------------------------------------------

def test_una_ruta_sin_alternativas_sigue_igual():
    assert _resolve_context_path(CONTEXTO, "_selected_entities_data.embarcacion_ids.0.nombre_embarcacion") == "AJUA"


def test_el_comodin_sigue_recorriendo_la_lista():
    """El comodín llegó con otro cambio; las alternativas no pueden estorbarlo."""
    valores = _resolve_context_path(CONTEXTO, "_selected_entities_data.embarcacion_ids.*.matricula")

    assert valores == ["MZT-1", "MZT-2"]


def test_una_ruta_que_no_existe_sigue_dando_none():
    assert _resolve_context_path(CONTEXTO, "_selected_entities_data.embarcacion_ids.0.no_existe") is None


# ---------------------------------------------------------------------------
# Alternativas
# ---------------------------------------------------------------------------

def test_toma_la_primera_que_resuelve():
    ruta = ("_selected_entities_data.embarcacion_ids.0.nombre"
            "|_selected_entities_data.embarcacion_ids.0.nombre_embarcacion")

    assert _resolve_context_path(CONTEXTO, ruta) == "AJUA"


def test_respeta_el_orden_declarado():
    """La primera que resuelve gana, aunque las siguientes también resolverían."""
    ruta = ("_selected_entities_data.embarcacion_ids.0.nombre_embarcacion"
            "|_selected_entities_data.embarcacion_ids.0.matricula")

    assert _resolve_context_path(CONTEXTO, ruta) == "AJUA"


def test_persona_moral_cae_en_la_segunda():
    """
    Ninguna ruta sola cubre a todos: 25 de 38 traen `nombre_completo` y 13
    `razon_social`. Juntas cubren a los 38.
    """
    ruta = ("_selected_entities_data.pescador_rnpa_ids.0.nombre_completo"
            "|_selected_entities_data.pescador_rnpa_ids.0.razon_social")

    assert _resolve_context_path(CONTEXTO, ruta) == "Pesquera del Golfo S.A."


def test_tolera_espacios_alrededor_del_separador():
    assert _resolve_context_path(CONTEXTO, "no.existe | _selected_entities_data.embarcacion_ids.0.eslora") == 12.5


def test_si_ninguna_resuelve_devuelve_none():
    assert _resolve_context_path(CONTEXTO, "no.existe|tampoco.existe") is None


def test_conserva_el_tipo_del_valor():
    """
    A diferencia del nombre, aquí el valor no se convierte a texto: un mapeo puede
    llevar números, listas o el dict de un domicilio al documento.
    """
    assert _resolve_context_path(CONTEXTO, "no.existe|_selected_entities_data.embarcacion_ids.0.tonelaje_bruto") == 8


def test_las_alternativas_funcionan_con_comodin():
    ruta = "_selected_entities_data.embarcacion_ids.*.nombre|_selected_entities_data.embarcacion_ids.*.nombre_embarcacion"

    assert _resolve_context_path(CONTEXTO, ruta) == ["AJUA", "REINA II"]


# ---------------------------------------------------------------------------
# Qué cuenta como "no resolvió"
# ---------------------------------------------------------------------------

def test_un_vacio_no_cuenta_como_resuelto():
    """
    Una cadena vacía en el documento es igual de inútil que el campo ausente, y
    peor: oculta que había una alternativa buena detrás.
    """
    assert _resolve_context_path(CONTEXTO, "vacio|_selected_entities_data.embarcacion_ids.0.matricula") == "MZT-1"


def test_una_lista_vacia_tampoco_cuenta():
    assert _resolve_context_path(CONTEXTO, "lista_vacia|_selected_entities_data.embarcacion_ids.0.matricula") == "MZT-1"


def test_un_cero_si_cuenta_como_resuelto():
    """Cero es un dato: una eslora de 0 es un error de captura, no un campo vacío."""
    contexto = {"a": {"b": 0}, "c": {"d": 99}}

    assert _resolve_context_path(contexto, "a.b|c.d") == 0


def test_un_falso_si_cuenta_como_resuelto():
    contexto = {"a": {"b": False}, "c": {"d": True}}

    assert _resolve_context_path(contexto, "a.b|c.d") is False
