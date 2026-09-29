"""
Hay selecciones que el sistema no puede hacer en lugar del ciudadano.

El selector tiene una regla de conveniencia: si el requisito pide exactamente una
entidad y el ciudadano tiene exactamente una, la elige solo y **se salta la
pantalla entera**. Para una embarcación cuando solo hay una, eso ahorra un paso
que no aporta nada.

Para la identidad y el RNPA no. Un trámite se presenta **a nombre de** una persona
física o moral, y esa es la decisión con consecuencias jurídicas de todo el
expediente: quién queda como titular del permiso, a quién se le factura, quién
responde. Decidirla en silencio porque "solo había una opción" le quita al
ciudadano el único momento en que podía darse cuenta de que va a nombre de quien
no quería —y la mayoría de la gente tiene varias personas morales además de la
suya—.

`auto_select` no servía para esto: solo **añade** autoselección (Regla 2), nunca la
quita. No había forma de exigir la confirmación.

`always_confirm` la exige. Es por requisito y no global a propósito: la regla de
conveniencia sigue valiendo donde no hay nada que confirmar, y los otros tenants
no cambian de comportamiento.

Son pruebas puras: no tocan Mongo.
"""

import os
import sys
from types import SimpleNamespace

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.workflows.operators.entity_picker_operator import EntityPickerOperator


def _picker(requirements):
    return EntityPickerOperator(task_id="pick", requirements=requirements)


def _entidad(entity_id="e1", tipo="pescador_rnpa"):
    return SimpleNamespace(entity_id=entity_id, entity_type=tipo, name="X", status="active", data={})


REQ_RNPA = {
    "entity_type": "pescador_rnpa",
    "min_count": 1,
    "max_count": 1,
    "store_as": "pescador_rnpa_ids",
}


# ---------------------------------------------------------------------------
# La conveniencia que se conserva
# ---------------------------------------------------------------------------

def test_sin_la_bandera_una_sola_entidad_se_autoselecciona():
    """La Regla 1 sigue valiendo donde no hay nada que confirmar."""
    op = _picker([REQ_RNPA])

    assert op._can_auto_select_single_requirement([_entidad()], REQ_RNPA) is True


def test_la_bandera_no_estorba_cuando_hay_varias():
    """Con varias candidatas nunca hubo autoselección; la bandera no cambia eso."""
    req = {**REQ_RNPA, "always_confirm": True}
    op = _picker([req])

    assert op._can_auto_select_single_requirement([_entidad("e1"), _entidad("e2")], req) is False


# ---------------------------------------------------------------------------
# Lo que la bandera impide
# ---------------------------------------------------------------------------

def test_con_la_bandera_una_sola_entidad_igual_se_pregunta():
    """
    Es el punto entero: el ciudadano tiene un solo RNPA y aun así se le muestra,
    para que confirme a nombre de quién va el trámite.
    """
    req = {**REQ_RNPA, "always_confirm": True}
    op = _picker([req])

    assert op._can_auto_select_single_requirement([_entidad()], req) is False


def test_la_bandera_gana_sobre_auto_select():
    """
    `auto_select` pide lo contrario. Ante la contradicción manda la confirmación:
    equivocarse de titular es más caro que un paso de más.
    """
    req = {**REQ_RNPA, "auto_select": True, "always_confirm": True}
    op = _picker([req])

    assert op._can_auto_select_single_requirement([_entidad(), _entidad("e2")], req) is False


def test_un_requisito_con_bandera_bloquea_la_autoseleccion_del_conjunto():
    """
    La decisión es de todo o nada: si un requisito exige confirmarse, la pantalla
    se muestra y con ella todos los demás. Si no, el RNPA se confirmaría pero la
    embarcación se habría elegido sola sin que el ciudadano la viera.
    """
    req_rnpa = {**REQ_RNPA, "always_confirm": True}
    req_emb = {
        "entity_type": "embarcacion_registrada",
        "min_count": 1,
        "max_count": 1,
        "store_as": "embarcacion_ids",
    }
    op = _picker([req_rnpa, req_emb])

    disponibles = {
        "pescador_rnpa_ids": [_entidad("r1")],
        "embarcacion_ids": [_entidad("b1", "embarcacion_registrada")],
    }

    assert op._can_auto_select_all_requirements(disponibles, [req_rnpa, req_emb]) is False


def test_sin_banderas_el_conjunto_si_se_autoselecciona():
    req_emb = {
        "entity_type": "embarcacion_registrada",
        "min_count": 1,
        "max_count": 1,
        "store_as": "embarcacion_ids",
    }
    op = _picker([REQ_RNPA, req_emb])

    disponibles = {
        "pescador_rnpa_ids": [_entidad("r1")],
        "embarcacion_ids": [_entidad("b1", "embarcacion_registrada")],
    }

    assert op._can_auto_select_all_requirements(disponibles, [REQ_RNPA, req_emb]) is True


# ---------------------------------------------------------------------------
# La bandera viaja al formulario
# ---------------------------------------------------------------------------

def test_el_formulario_declara_que_el_requisito_exige_confirmarse():
    """
    El portal necesita saberlo para redactar la pantalla: pedir que se confirme no
    se lee igual que pedir que se elija.
    """
    req = {**REQ_RNPA, "always_confirm": True, "display_title": "Registro RNPA"}
    op = _picker([req])

    form = op._generate_selection_form({"pescador_rnpa_ids": 1})

    campo = next(c for c in form["fields"] if c["name"] == "pescador_rnpa_ids")
    assert campo["always_confirm"] is True


def test_un_requisito_normal_no_declara_la_bandera():
    op = _picker([REQ_RNPA])

    form = op._generate_selection_form({"pescador_rnpa_ids": 3})

    campo = next(c for c in form["fields"] if c["name"] == "pescador_rnpa_ids")
    assert campo.get("always_confirm") is False
