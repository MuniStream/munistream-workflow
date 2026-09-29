"""
Búsqueda de entidades para el personal del admin.

Hasta ahora las entidades solo se consultaban desde el portal ciudadano, siempre
acotadas a su dueño (`EntityService.get_entity(entity_id, customer_id)`). El
admin no tenía forma de encontrar una embarcación, un permiso o una credencial
sin conocer de antemano de quién era.

Estas pruebas fijan la construcción de la consulta, que es donde están los
errores caros: un texto del usuario metido crudo en un regex de Mongo se
comporta como comodín —un punto casa con cualquier cosa— y con la entrada
adecuada degrada la base.

Son puras: no tocan Mongo.
"""

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.services.entity_search import CAMPOS_BUSCABLES, construir_consulta


def test_sin_texto_no_hay_busqueda():
    assert construir_consulta() == {}


def test_el_texto_en_blanco_no_filtra():
    assert construir_consulta(q="   ") == {}


def test_busca_en_nombre_identificador_y_campos_de_datos():
    consulta = construir_consulta(q="abc")

    campos = {list(c.keys())[0] for c in consulta["$or"]}
    assert "name" in campos
    assert "entity_id" in campos
    for campo in CAMPOS_BUSCABLES:
        assert f"data.{campo}" in campos


def test_la_busqueda_ignora_mayusculas():
    consulta = construir_consulta(q="abc")

    assert all(c[list(c)[0]]["$options"] == "i" for c in consulta["$or"])


def test_el_texto_del_usuario_se_escapa():
    """
    Sin escapar, un punto casa con cualquier carácter y `.*` con todo: la
    búsqueda devolvería resultados que no corresponden, y una entrada como
    `(a+)+` es un regex catastrófico contra la base.
    """
    consulta = construir_consulta(q="a.b(c")

    patron = consulta["$or"][0][list(consulta["$or"][0])[0]]["$regex"]
    assert "a\\.b\\(c" == patron


def test_los_espacios_de_los_bordes_se_recortan():
    consulta = construir_consulta(q="  abc  ")

    patron = consulta["$or"][0][list(consulta["$or"][0])[0]]["$regex"]
    assert patron == "abc"


def test_el_tipo_y_el_estado_se_combinan_con_la_busqueda():
    consulta = construir_consulta(q="abc", entity_type="embarcacion_registrada", status="active")

    assert consulta["entity_type"] == "embarcacion_registrada"
    assert consulta["status"] == "active"
    assert "$or" in consulta


def test_se_puede_acotar_a_un_dueno():
    consulta = construir_consulta(owner_user_id="cliente-1")

    assert consulta["owner_user_id"] == "cliente-1"


def test_varios_tipos_a_la_vez():
    consulta = construir_consulta(entity_type=["permiso_pesca_comercial", "guia_pesca"])

    assert consulta["entity_type"] == {"$in": ["permiso_pesca_comercial", "guia_pesca"]}


def test_los_filtros_del_requisito_se_aplican():
    """
    Los pickers declaran `filters` como `{"entity_subtype": "permiso_simplificado"}`.
    Se siguen las mismas reglas que `EntityService.find_entities`: una clave sin
    punto se asume campo de `data`.
    """
    consulta = construir_consulta(filtros={"entity_subtype": "permiso_simplificado"})

    assert consulta["data.entity_subtype"] == "permiso_simplificado"


def test_un_filtro_con_ruta_explicita_se_respeta():
    consulta = construir_consulta(filtros={"data.tipo": "mayor"})

    assert consulta["data.tipo"] == "mayor"
    assert "data.data.tipo" not in consulta


def test_un_filtro_con_varios_valores_usa_in():
    consulta = construir_consulta(filtros={"tipo": ["mayor", "menor"]})

    assert consulta["data.tipo"] == {"$in": ["mayor", "menor"]}


def test_los_filtros_conviven_con_la_busqueda():
    consulta = construir_consulta(q="abc", filtros={"tipo": "mayor"})

    assert "$or" in consulta
    assert consulta["data.tipo"] == "mayor"
