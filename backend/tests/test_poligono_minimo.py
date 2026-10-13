"""Un campo `geo` en modo polígono no acepta figuras con menos de 3 vértices
(no son polígonos: un GeoJSON Polygon de 1 o 2 puntos es inválido), y el
trámite puede exigir más con `min_vertices` (p. ej. 4 para una superficie)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.workflows.operators.user_input import UserInputOperator


def _op(**extra):
    campo = {"name": "predio", "label": "Polígono del predio", "type": "geo", "geo_mode": "polygon", **extra}
    return UserInputOperator(task_id="t", form_config={"fields": [campo]}, required_fields=[])


def _poligono(n, cerrado=True):
    puntos = [[-102.0 + i * 0.01, 23.0 + (i % 2) * 0.01] for i in range(n)]
    if cerrado and n:
        puntos.append(puntos[0])
    return {"type": "Polygon", "coordinates": [puntos]}


def test_rechaza_poligono_de_un_vertice():
    errores = _op()._validate_input({"predio": _poligono(1)})
    assert any("al menos 3 vértices" in e for e in errores), errores


def test_rechaza_poligono_de_dos_vertices_sin_cerrar():
    errores = _op()._validate_input({"predio": _poligono(2, cerrado=False)})
    assert any("al menos 3 vértices" in e for e in errores), errores


def test_acepta_triangulo_cerrado():
    assert _op()._validate_input({"predio": _poligono(3)}) == []


def test_min_vertices_del_tramite():
    errores = _op(min_vertices=4)._validate_input({"predio": _poligono(3)})
    assert any("al menos 4 vértices" in e for e in errores), errores
    assert _op(min_vertices=4)._validate_input({"predio": _poligono(4)}) == []


def test_un_punto_no_se_valida_como_poligono():
    op = UserInputOperator(task_id="t", required_fields=[], form_config={"fields": [
        {"name": "ubicacion", "label": "Ubicación", "type": "geo", "geo_mode": "point"}]})
    assert op._validate_input({"ubicacion": {"type": "Point", "coordinates": [-102.0, 23.0]}}) == []
