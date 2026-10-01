"""
Candidatas del selector de entidades: se piden, no se persisten.

El picker metía el catálogo entero dentro del `form_config`, que el executor
mezcla al contexto y Mongo persiste, y además lo duplicaba en
`{task_id}_discovery_cache` y en un `pre_task_context_snapshots` por cada tarea
posterior del DAG. Con una entidad pesando ~107 KB de media, unas decenas de
candidatas bastan para empujar la instancia contra el tope de 16 MB de BSON —
que es justo lo que la deja atorada, como documenta el propio executor.

Aquí vive lo que necesita el endpoint que sirve las candidatas paginadas: dónde
está configurado cada requisito y qué se manda de cada entidad.

Los campos a mostrar se resuelven **en el servidor**. El operador probaba
`hasattr(entidad, campo)` mientras la tarjeta del portal solo leía
`data[campo]`, así que un `display_fields` con `name` o con rutas con punto no
pintaba nada. Resolviéndolos aquí, la tarjeta solo renderiza lo que recibe.
"""

from typing import Any, Dict, List, Optional, Tuple

from .entity_validity import vigencia_de


def encontrar_requisito(dag, store_as: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    Tarea y requisito del DAG que declaran este `store_as`.

    Se busca por `store_as` y no por `task_id` porque un DAG puede tener varios
    pickers (`credencial_rnpa` y `pesca_deportiva` tienen dos), y porque es el
    `store_as` lo que el portal conoce: es el nombre del campo del formulario.
    """
    for task_id, tarea in (getattr(dag, "tasks", None) or {}).items():
        for requisito in (getattr(tarea, "requirements", None) or []):
            if isinstance(requisito, dict) and requisito.get("store_as") == store_as:
                return task_id, requisito
    return None


# Claves que el picker añade al fotografiar la entidad seleccionada y que no
# viven en `data`. Se aceptan también aquí para que un `display_fields` pueda
# usar la misma alternativa en la tarjeta y en la confirmación.
_SINONIMOS_DE_ENTIDAD = {
    "_entity_name": "name",
    "_entity_id": "entity_id",
    "_entity_type": "entity_type",
}


def _valor_simple(entidad, ruta: str) -> Any:
    atributo = _SINONIMOS_DE_ENTIDAD.get(ruta, ruta)
    if "." not in atributo and hasattr(entidad, atributo):
        return getattr(entidad, atributo)

    nodo: Any = getattr(entidad, "data", None) or {}
    for parte in ruta.split("."):
        if not isinstance(nodo, dict) or parte not in nodo:
            return None
        nodo = nodo[parte]
    return nodo


def _valor_en(entidad, ruta: str) -> Any:
    """
    Resuelve un campo a mostrar: atributo de la entidad, clave de `data`, o una
    ruta con punto dentro de `data`.

    Admite alternativas separadas por `|` —`"nombre|_entity_name"`— y devuelve
    la primera con dato. El mismo contrato que usa la confirmación: una entidad
    llama `nombre` a lo que otra llama `nombre_completo`, y sin alternativas la
    tarjeta se quedaba en blanco justo en el campo que identifica a la persona.
    """
    for alternativa in ruta.split("|"):
        alternativa = alternativa.strip()
        if not alternativa:
            continue
        valor = _valor_simple(entidad, alternativa)
        if valor not in (None, "", [], {}):
            return valor
    return None


def resumen_de_candidata(entidad, display_fields: List[str]) -> Dict[str, Any]:
    """
    Lo que la tarjeta pinta, y nada más. Nunca el `data` íntegro.

    Los campos ausentes se omiten en vez de devolverse vacíos: en guía de pesca,
    `fecha_arribo` y `fecha` son alternativas según el tipo de aviso, y el que no
    aplique dejaría un renglón en blanco en la tarjeta.
    """
    campos = []
    for ruta in (display_fields or []):
        valor = _valor_en(entidad, ruta)
        if valor in (None, "", [], {}):
            continue
        # La etiqueta sale de la PRIMERA alternativa: `|` es contrato de
        # resolución, no de presentación, y rotular la cadena entera dejaba
        # "Nombre| entity name" en la tarjeta.
        campos.append({"field": ruta.split("|")[0].strip(), "value": valor})

    return {
        "entity_id": getattr(entidad, "entity_id", None),
        "entity_type": getattr(entidad, "entity_type", None),
        "name": getattr(entidad, "name", None),
        "status": getattr(entidad, "status", None),
        "fields": campos,
        "validity": vigencia_de(entidad),
    }
