"""
El nombre de la entidad no puede ser una variable sin expandir.

En la cartera del ciudadano aparecen documentos llamados así:

    _selected_entities_data.embarcacion_ids.0.nombre

Es el `name_source` del trámite tal cual, sin resolver: la ruta apunta a un campo
que la entidad seleccionada no trae, y el nombre se quedó con la ruta literal. Son
4 de las 58 entidades de la base, y el ciudadano ve esa cadena donde debería ir el
nombre de su embarcación.

Ya existía una guarda para las rutas peladas, pero **excluye explícitamente las
plantillas** `{{ }}`, y `_resolve_template_string` deja la llave puesta cuando la
variable no resuelve. Varios trámites usan esa forma:

    name_source="Permiso de Pesca Comercial — {{_selected_entities_data.pescador_rnpa_ids.0.nombre_completo}}"

así que el mismo defecto sigue abierto por el otro lado, y ahí la guarda no mira.

La regla que fijan estas pruebas es una sola: **lo que sale nunca parece código**.
Ni llaves, ni rutas con puntos, ni separadores colgando de un hueco que no se
llenó. Y cuando no queda nada utilizable, un nombre legible antes que la clave
técnica del tipo — quien lee la cartera no tiene por qué saber qué es un
`permiso_pesca_comercial`.

Son pruebas puras: no tocan Mongo.
"""

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.services.entity_naming import (
    hay_sin_resolver,
    nombre_legible_de_tipo,
    resolver_nombre,
)

CONTEXTO = {
    "_selected_entities_data": {
        "embarcacion_ids": [{"nombre": "Doña Petra", "matricula": "MZT-1234"}],
        "pescador_rnpa_ids": [{"nombre_completo": "María Sánchez"}],
    },
    "folio_solicitud": "CNP-2026-0042",
}


def _resolver(name_source, contexto=None, datos=None, tipo="permiso_pesca_comercial"):
    return resolver_nombre(
        name_source,
        context=CONTEXTO if contexto is None else contexto,
        entity_data=datos or {},
        entity_type=tipo,
    )


# ---------------------------------------------------------------------------
# Lo que ya funcionaba y no se puede romper
# ---------------------------------------------------------------------------

def test_una_ruta_que_resuelve_da_el_valor():
    assert _resolver("_selected_entities_data.embarcacion_ids.0.nombre") == "Doña Petra"


def test_una_plantilla_que_resuelve_queda_completa():
    plantilla = "Permiso de Pesca Comercial — {{_selected_entities_data.pescador_rnpa_ids.0.nombre_completo}}"

    assert _resolver(plantilla) == "Permiso de Pesca Comercial — María Sánchez"


def test_un_nombre_estatico_se_respeta():
    """Muchos trámites pasan un título fijo; no hay nada que resolver ahí."""
    assert _resolver("Título de Concesión") == "Título de Concesión"


def test_un_campo_de_la_entidad_gana_sobre_el_contexto():
    """
    `name_source` puede nombrar un campo de la propia entidad que se está
    creando; ese dato es más específico que cualquier cosa del contexto.
    """
    assert _resolver("nombre", datos={"nombre": "Embarcación Guadalupe"}) == "Embarcación Guadalupe"


# ---------------------------------------------------------------------------
# El defecto reportado: la ruta pelada que no resuelve
# ---------------------------------------------------------------------------

def test_una_ruta_que_no_resuelve_no_se_usa_literal():
    """Es lo que ve el ciudadano hoy en su cartera."""
    nombre = _resolver("_selected_entities_data.embarcacion_ids.0.folio_inexistente")

    assert "_selected_entities_data" not in nombre
    assert nombre != "_selected_entities_data.embarcacion_ids.0.folio_inexistente"


def test_una_ruta_que_no_resuelve_cae_en_un_dato_de_la_entidad():
    nombre = _resolver(
        "_selected_entities_data.embarcacion_ids.0.folio_inexistente",
        datos={"nombre": "Doña Petra"},
    )

    assert nombre == "Doña Petra"


# ---------------------------------------------------------------------------
# El hueco que la guarda anterior no miraba: las plantillas
# ---------------------------------------------------------------------------

def test_una_plantilla_sin_resolver_no_deja_llaves():
    """
    La guarda anterior exigía `"{{" not in name_source`, así que este caso pasaba
    de largo y el nombre quedaba con la llave a la vista.
    """
    plantilla = "Permiso de Pesca Comercial — {{_selected_entities_data.pescador_rnpa_ids.0.nombre_completo}}"

    nombre = _resolver(plantilla, contexto={})

    assert "{{" not in nombre and "}}" not in nombre


def test_una_plantilla_a_medias_conserva_lo_que_sí_resolvió():
    plantilla = "Permiso de Pesca Comercial — {{no.existe}}"

    assert _resolver(plantilla, contexto={}) == "Permiso de Pesca Comercial"


def test_no_queda_el_separador_colgando():
    """
    Quitar el hueco y dejar "Permiso de Pesca Comercial —" delata el fallo igual
    que dejar la llave. El separador solo tiene sentido si hay dos lados.
    """
    for separador in ("—", "-", ":", "·", "|", ","):
        nombre = _resolver(f"Permiso {separador} {{{{no.existe}}}}", contexto={})
        assert not nombre.endswith(separador), f"quedó colgando el {separador!r}: {nombre!r}"


def test_un_hueco_al_principio_tampoco_deja_separador():
    assert _resolver("{{no.existe}} — Permiso de Pesca", contexto={}) == "Permiso de Pesca"


def test_una_plantilla_que_no_resuelve_nada_cae_al_respaldo():
    nombre = _resolver("{{no.existe}}", contexto={}, datos={"nombre": "Doña Petra"})

    assert nombre == "Doña Petra"


# ---------------------------------------------------------------------------
# El respaldo
# ---------------------------------------------------------------------------

def test_el_respaldo_prefiere_un_campo_con_significado():
    datos = {"eslora_metros": 12, "matricula": "MZT-1234", "nombre": "Doña Petra"}

    assert _resolver("{{no.existe}}", contexto={}, datos=datos) == "Doña Petra"


def test_sin_nombre_sirve_la_matricula():
    """Un identificador oficial nombra el documento mejor que su tipo."""
    assert _resolver("{{no.existe}}", contexto={}, datos={"matricula": "MZT-1234"}) == "MZT-1234"


def test_sin_nada_utilizable_el_tipo_va_legible():
    """
    `permiso_pesca_comercial` es la clave técnica; quien abre su cartera no tiene
    por qué leer eso.
    """
    nombre = _resolver("{{no.existe}}", contexto={}, datos={})

    assert nombre == "Permiso de pesca comercial"


def test_el_respaldo_ignora_valores_que_no_nombran_nada():
    """Un booleano o un número suelto no es un nombre."""
    datos = {"nombre": True, "matricula": "", "folio": 0}

    assert _resolver("{{no.existe}}", contexto={}, datos=datos) == "Permiso de pesca comercial"


# ---------------------------------------------------------------------------
# La regla de fondo, dicha de una vez
# ---------------------------------------------------------------------------

def test_el_nombre_nunca_parece_codigo():
    casos = [
        "_selected_entities_data.embarcacion_ids.0.folio_inexistente",
        "{{no.existe}}",
        "Permiso — {{no.existe}}",
        "collect_datos.campo_que_no_esta",
    ]
    for caso in casos:
        nombre = _resolver(caso, contexto={})
        assert "{{" not in nombre, caso
        assert not hay_sin_resolver(nombre), f"{caso!r} produjo {nombre!r}"


def test_nombre_legible_de_tipo():
    assert nombre_legible_de_tipo("permiso_pesca_comercial") == "Permiso de pesca comercial"
    assert nombre_legible_de_tipo("embarcacion_registrada") == "Embarcación registrada"
    assert nombre_legible_de_tipo("") == "Documento"


def test_hay_sin_resolver_reconoce_las_dos_formas():
    assert hay_sin_resolver("{{a.b}}") is True
    assert hay_sin_resolver("_selected_entities_data.embarcacion_ids.0.nombre") is True
    assert hay_sin_resolver("collect_datos.campo") is True

    assert hay_sin_resolver("Doña Petra") is False
    assert hay_sin_resolver("Permiso de Pesca Comercial — María Sánchez") is False
    # Un nombre con punto pero con espacios es prosa, no una ruta.
    assert hay_sin_resolver("Sociedad Cooperativa S.C. de R.L.") is False


# ---------------------------------------------------------------------------
# Alternativas: un solo campo nunca alcanza
# ---------------------------------------------------------------------------

def test_alternativas_toman_la_primera_que_resuelve():
    """
    Los trámites nombran al solicitante con `nombre_completo`, pero eso solo
    existe en las personas físicas: las morales traen `razon_social`. Medido en
    la base, 25 de 38 selecciones traen una y 13 la otra —ninguna las dos, ninguna
    ninguna—, así que cualquier campo solo deja fuera a un tercio de la gente.
    """
    contexto = {"_selected_entities_data": {"pescador_rnpa_ids": [{"razon_social": "Pesquera del Golfo S.A."}]}}

    nombre = _resolver(
        "_selected_entities_data.pescador_rnpa_ids.0.nombre_completo"
        "|_selected_entities_data.pescador_rnpa_ids.0.razon_social",
        contexto=contexto,
    )

    assert nombre == "Pesquera del Golfo S.A."


def test_alternativas_respetan_el_orden():
    contexto = {"_selected_entities_data": {"pescador_rnpa_ids": [
        {"nombre_completo": "María Sánchez", "razon_social": "No debería usarse"}]}}

    nombre = _resolver(
        "_selected_entities_data.pescador_rnpa_ids.0.nombre_completo"
        "|_selected_entities_data.pescador_rnpa_ids.0.razon_social",
        contexto=contexto,
    )

    assert nombre == "María Sánchez"


def test_alternativas_dentro_de_una_plantilla():
    contexto = {"_selected_entities_data": {"pescador_rnpa_ids": [{"razon_social": "Pesquera del Golfo S.A."}]}}

    nombre = _resolver(
        "Concesión — {{_selected_entities_data.pescador_rnpa_ids.0.nombre_completo"
        "|_selected_entities_data.pescador_rnpa_ids.0.razon_social}}",
        contexto=contexto,
    )

    assert nombre == "Concesión — Pesquera del Golfo S.A."


def test_alternativas_toleran_espacios():
    contexto = {"a": {"b": "Valor"}}

    assert _resolver("no.existe | a.b", contexto=contexto) == "Valor"


def test_si_ninguna_alternativa_resuelve_se_va_al_respaldo():
    nombre = _resolver("no.existe|tampoco.existe", contexto={}, datos={"matricula": "MZT-1234"})

    assert nombre == "MZT-1234"
    assert "|" not in nombre
