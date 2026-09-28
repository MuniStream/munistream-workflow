"""
Búsqueda de trámites en el admin.

El buscador mandaba el texto como `instance_id`, así que solo encontraba por
UUID exacto: no se podía buscar por el nombre del ciudadano, su correo ni el
folio. Y el listado **sí** muestra `citizen_name` y `citizen_email`, porque
`enrich_instances` los resuelve contra la colección de clientes — pero lo hace
*después* de consultar y paginar, así que no se podía filtrar por ellos.

De ahí la forma de la solución: los nombres se resuelven primero a ids de
cliente y la consulta filtra por `user_id`, además de mirar las copias que el
propio contexto de la instancia guarda.

Son pruebas puras: no tocan Mongo.
"""

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.services.instance_search import construir_consulta


def test_sin_texto_ni_filtros_no_hay_consulta():
    assert construir_consulta() == {}


def test_el_texto_en_blanco_no_filtra():
    assert construir_consulta(q="  ") == {}


def test_busca_por_identificador_y_por_los_datos_del_ciudadano_en_el_contexto():
    consulta = construir_consulta(q="dolores")

    campos = {list(c)[0] for c in consulta["$or"]}
    assert "instance_id" in campos
    assert "context.customer_name" in campos
    assert "context.customer_email" in campos


def test_los_ids_de_cliente_resueltos_entran_en_la_busqueda():
    """
    El nombre del ciudadano vive en la colección de clientes, no en la
    instancia: se resuelve fuera y se inyecta aquí.
    """
    consulta = construir_consulta(q="dolores", user_ids=["c1", "c2"])

    assert {"user_id": {"$in": ["c1", "c2"]}} in consulta["$or"]


def test_sin_coincidencias_de_cliente_la_busqueda_sigue_por_los_otros_campos():
    consulta = construir_consulta(q="dolores", user_ids=[])

    assert "$or" in consulta
    assert not any("user_id" in c for c in consulta["$or"])


def test_el_texto_del_usuario_se_escapa():
    consulta = construir_consulta(q="a.b(c")

    patron = consulta["$or"][0][list(consulta["$or"][0])[0]]["$regex"]
    assert patron == "a\\.b\\(c"


def test_los_filtros_se_combinan_con_la_busqueda():
    consulta = construir_consulta(
        q="dolores", status="paused", workflow_id="tramite_01_001_concesion_pesca_comercial"
    )

    assert consulta["status"] == "paused"
    assert consulta["workflow_id"] == "tramite_01_001_concesion_pesca_comercial"
    assert "$or" in consulta


def test_los_filtros_solos_funcionan_sin_texto():
    consulta = construir_consulta(status="paused")

    assert consulta == {"status": "paused"}
