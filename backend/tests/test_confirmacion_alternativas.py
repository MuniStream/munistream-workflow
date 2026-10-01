"""El resumen de confirmación resuelve alternativas, como `name_source`.

Las entidades de una misma clase NO son homogéneas: los registros RNPA de
distinta época llevan `nombre_completo` o `razon_social`, y un requisito que
acepta persona física u moral tiene que enseñar `nombre`/`curp` o
`razon_social`/`rfc` según el caso. Con una sola ruta por campo, el ciudadano
veía renglones vacíos en la confirmación aunque el dato estuviera ahí con otro
nombre.

No se omiten los campos sin valor a propósito: esconderlos taparía que falta un
dato. Lo que se hace es buscarlo donde de verdad está.

Pruebas puras: no tocan Mongo ni Keycloak.
"""

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.workflows.operators.confirmation_operator import _resolve_path  # noqa: E402

FISICA = {"e": {"nombre": "Ana Pérez", "curp": "PEXA800101MDFRXN04"}}
MORAL = {"e": {"razon_social": "Pesquera del Golfo S.A.", "rfc": "PGO010101AAA"}}


def test_gana_la_primera_que_resuelve():
    assert _resolve_path(FISICA, "e.nombre|e.razon_social") == "Ana Pérez"
    assert _resolve_path(MORAL, "e.nombre|e.razon_social") == "Pesquera del Golfo S.A."


def test_una_alternativa_vacia_no_cuenta():
    """Una cadena vacía es tan inútil como la ausencia: se pasa a la siguiente."""
    ctx = {"e": {"nombre": "", "razon_social": "Pesquera del Golfo S.A."}}
    assert _resolve_path(ctx, "e.nombre|e.razon_social") == "Pesquera del Golfo S.A."


def test_sin_ninguna_devuelve_none_y_el_campo_se_ve_vacio():
    """No se inventa un valor: el renglón queda vacío y eso es visible."""
    assert _resolve_path(FISICA, "e.inexistente|e.tampoco") is None


def test_se_respetan_los_espacios_alrededor():
    assert _resolve_path(FISICA, "e.inexistente | e.nombre") == "Ana Pérez"


def test_una_ruta_sin_alternativas_sigue_igual():
    assert _resolve_path(FISICA, "e.curp") == "PEXA800101MDFRXN04"
    assert _resolve_path({"u": [{"filename": "a.pdf"}]}, "u.0.filename") == "a.pdf"
