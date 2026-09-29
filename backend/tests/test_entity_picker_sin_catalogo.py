"""
El picker deja de meter el catálogo en el contexto, y tres defectos que matan trámites.

**El catálogo.** `_generate_selection_form` metía cada candidata completa —con su
`data`— dentro del `form_config`. El executor mezcla la salida al contexto y Mongo
lo persiste, y encima se duplicaba en `{task_id}_discovery_cache` y en un
`pre_task_context_snapshots` por cada tarea posterior. Con una entidad pesando
~107 KB de media, unas decenas bastan para pasar el tope de 16 MB de BSON y dejar
la instancia atorada. Ahora el formulario lleva solo el descriptor del requisito y
el portal pide las candidatas al endpoint.

**Defecto 1: el caché nunca se invalidaba cuando faltaban entidades.** Solo se
invalidaba al validar selecciones. En el camino `missing_entities` no se tocaba,
no había TTL y el picker es event-driven: el ciudadano creaba lo que le faltaba,
volvía, y el picker releía el snapshot viejo y le seguía diciendo que le faltaba.
Para siempre. Es la causa de la instancia `19dae91b`.

**Defecto 2: `_last_form_config` era estado en un singleton compartido.**
`DAGInstance` guarda el DAG sin copiarlo, así que todas las instancias del
trámite comparten el objeto operador. Si no existía al fallar una validación, el
paso terminaba en `FAILED` —que mata la instancia—; y si existía, podía ser el
formulario de otro ciudadano.

**Defecto 3: envío por pantallas.** Con un tipo por pantalla, cada una manda su
selección y el operador la fusiona, quedándose en espera hasta tenerlas todas.
Antes exigía que llegaran todas a la vez, así que un ciudadano que abandonaba a
media captura perdía lo ya elegido.

Son pruebas puras: no tocan Mongo.
"""

import os
import sys
from types import SimpleNamespace

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.workflows.operators.base import TaskStatus
from app.workflows.operators.entity_picker_operator import EntityPickerOperator

REQUISITOS = [
    {
        "entity_type": "pescador_rnpa",
        "min_count": 1,
        "max_count": 1,
        "store_as": "pescador_rnpa_ids",
        "display_title": "Registro RNPA",
        "display_fields": ["nombre_completo"],
    },
    {
        "entity_type": "embarcacion_registrada",
        "min_count": 1,
        "max_count": 5,
        "store_as": "embarcacion_ids",
        "display_title": "Embarcaciones",
        "display_fields": ["matricula"],
    },
]


def _picker():
    return EntityPickerOperator(task_id="pick_required_docs", requirements=REQUISITOS)


def _entidad(entity_id, tipo, nombre, **data):
    return SimpleNamespace(
        entity_id=entity_id, entity_type=tipo, name=nombre, status="active", data=data
    )


def _conteos(**kwargs):
    """Lo que el descubrimiento necesita saber ahora: cuántas hay, no cuáles."""
    return kwargs


# ---------------------------------------------------------------------------
# El formulario ya no lleva el catálogo
# ---------------------------------------------------------------------------

def test_el_formulario_no_lleva_las_candidatas():
    """Es el punto del cambio: el `form_config` deja de crecer con la cartera."""
    form = _picker()._generate_selection_form(
        _conteos(pescador_rnpa_ids=3, embarcacion_ids=120)
    )

    texto = str(form)
    assert "options" not in texto
    assert "entity_data" not in texto


def test_el_formulario_describe_cada_requisito():
    form = _picker()._generate_selection_form(
        _conteos(pescador_rnpa_ids=3, embarcacion_ids=120)
    )

    por_nombre = {c["name"]: c for c in form["fields"]}
    assert set(por_nombre) == {"pescador_rnpa_ids", "embarcacion_ids"}
    emb = por_nombre["embarcacion_ids"]
    assert emb["min_count"] == 1
    assert emb["max_count"] == 5
    assert emb["display_fields"] == ["matricula"]
    assert emb["total"] == 120


def test_cada_requisito_es_una_pantalla():
    """
    Un trámite puede pedir tres tipos a la vez; apilarlos en la misma pantalla
    es lo que hace ilegible el primer paso.
    """
    form = _picker()._generate_selection_form(
        _conteos(pescador_rnpa_ids=3, embarcacion_ids=120)
    )

    assert form["por_pantallas"] is True
    assert [c["name"] for c in form["fields"]] == ["pescador_rnpa_ids", "embarcacion_ids"]


def test_el_tipo_de_campo_depende_del_maximo():
    form = _picker()._generate_selection_form(
        _conteos(pescador_rnpa_ids=3, embarcacion_ids=120)
    )
    por_nombre = {c["name"]: c for c in form["fields"]}

    assert por_nombre["pescador_rnpa_ids"]["type"] == "entity_select"
    assert por_nombre["embarcacion_ids"]["type"] == "entity_multi_select"


# ---------------------------------------------------------------------------
# Defecto 1: nada de caché en el contexto
# ---------------------------------------------------------------------------

def test_no_queda_cache_de_descubrimiento_en_el_contexto():
    """
    El caché guardaba la cartera entera y solo se invalidaba al validar. Quien
    creaba la entidad que le faltaba y volvía, seguía viendo que le faltaba.
    """
    picker = _picker()
    contexto = {"user_id": "c1"}

    picker._store_discovery_cache(contexto, {"embarcacion_ids": [_entidad("e1", "embarcacion_registrada", "X")]})

    assert not [k for k in contexto if "discovery_cache" in k]


def test_el_descubrimiento_nunca_se_relee_de_un_snapshot_viejo():
    picker = _picker()
    contexto = {"user_id": "c1", "pick_required_docs_discovery_cache": {"embarcacion_ids": []}}

    assert picker._load_cached_discovery(contexto) is None


# ---------------------------------------------------------------------------
# Defecto 2: sin estado en el operador compartido
# ---------------------------------------------------------------------------

def test_el_formulario_de_rechazo_se_reconstruye_no_se_recuerda():
    """
    El operador es un singleton compartido entre instancias: recordar el último
    formulario en `self` lo mezcla entre ciudadanos, y si no está, el paso muere
    en FAILED.
    """
    picker = _picker()

    resultado = picker._rechazar_seleccion(
        _conteos(pescador_rnpa_ids=3, embarcacion_ids=120),
        errores=["Selecciona al menos 1 Embarcaciones"],
        previas={"embarcacion_ids": []},
    )

    assert resultado.status == TaskStatus.WAITING
    assert resultado.data["form_config"]["fields"]
    assert not hasattr(picker, "_last_form_config")


@pytest.mark.parametrize("entrada", [None, {}, {"pick_required_docs_selections": None}])
def test_ninguna_entrada_hace_que_el_paso_muera(entrada):
    """Un FAILED mata la instancia; el rechazo siempre vuelve a pedir."""
    picker = _picker()
    contexto = {"user_id": "c1"}
    if entrada is not None:
        contexto["pick_required_docs_input"] = entrada

    resultado = picker.execute(contexto)

    assert resultado.status != TaskStatus.FAILED


# ---------------------------------------------------------------------------
# Defecto 3: envío por pantallas, con memoria
# ---------------------------------------------------------------------------

def test_una_pantalla_enviada_no_borra_lo_ya_elegido():
    picker = _picker()
    contexto = {"selected_entities": {"pescador_rnpa_ids": ["p1"]}}

    fusionadas = picker._fusionar_selecciones(contexto, {"embarcacion_ids": ["e1", "e2"]})

    assert fusionadas == {"pescador_rnpa_ids": ["p1"], "embarcacion_ids": ["e1", "e2"]}


def test_reenviar_una_pantalla_sustituye_su_seleccion():
    picker = _picker()
    contexto = {"selected_entities": {"embarcacion_ids": ["e1", "e2"]}}

    fusionadas = picker._fusionar_selecciones(contexto, {"embarcacion_ids": ["e3"]})

    assert fusionadas["embarcacion_ids"] == ["e3"]


def test_faltando_una_pantalla_se_sigue_esperando():
    picker = _picker()

    assert picker._faltan_requisitos({"pescador_rnpa_ids": ["p1"]}) == ["embarcacion_ids"]


def test_con_todas_las_pantallas_ya_no_falta_nada():
    picker = _picker()

    completas = {"pescador_rnpa_ids": ["p1"], "embarcacion_ids": ["e1"]}
    assert picker._faltan_requisitos(completas) == []


def test_un_requisito_opcional_no_bloquea():
    picker = EntityPickerOperator(
        task_id="pick",
        requirements=[
            {"entity_type": "x", "min_count": 0, "max_count": 1, "store_as": "x_ids"},
            {"entity_type": "y", "min_count": 1, "max_count": 1, "store_as": "y_ids"},
        ],
    )

    assert picker._faltan_requisitos({"y_ids": ["y1"]}) == []


def test_los_errores_viajan_dentro_del_formulario():
    """
    `input_form` es literalmente el `form_config`: lo que quede en la raíz del
    TaskResult no llega al portal. Por eso el motivo del rechazo y lo ya elegido
    van dentro, no al lado.
    """
    picker = _picker()

    resultado = picker._rechazar_seleccion(
        _conteos(pescador_rnpa_ids=3, embarcacion_ids=120),
        errores=["Selecciona al menos 1 Embarcaciones"],
        previas={"pescador_rnpa_ids": ["p1"]},
    )

    form = resultado.data["form_config"]
    assert form["validation_errors"] == ["Selecciona al menos 1 Embarcaciones"]
    assert form["previous_selections"] == {"pescador_rnpa_ids": ["p1"]}
