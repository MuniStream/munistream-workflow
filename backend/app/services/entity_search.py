"""
Construcción de la consulta para buscar entidades desde el admin.

Hasta ahora las entidades solo se consultaban desde el portal ciudadano, siempre
acotadas a su dueño. El personal no tenía forma de encontrar una embarcación, un
permiso o una credencial sin conocer de antemano de quién era.

El texto del usuario se escapa siempre antes de entrar al regex. Sin escapar, un
punto casa con cualquier carácter y la búsqueda devuelve resultados que no
corresponden; y una entrada como `(a+)+` es un regex catastrófico que degrada la
base con una sola petición.

Sobre el rendimiento: un regex sin anclar no usa índice, así que esto recorre la
colección. Con los volúmenes de un tenant —decenas de miles de entidades— es
aceptable y mucho más simple que la alternativa. Si llega a hacer falta, el
siguiente paso es un índice de texto de Mongo sobre `name` y los campos
identificadores, asumiendo que entonces la búsqueda pasa a ser por palabra
completa y deja de encontrar coincidencias parciales.
"""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

# Campos de `data` donde vive lo que alguien teclearía para buscar: los
# identificadores oficiales de cada tipo de entidad. No se busca en todo `data`
# a propósito — ahí hay imágenes en base64 y estructuras anidadas que no son
# texto buscable y harían la consulta mucho más cara.
CAMPOS_BUSCABLES = (
    "rfc",
    "curp",
    "rnpa",
    "matricula",
    "folio",
    "nombre_completo",
    "razon_social",
    "numero_permiso",
    "numero_titulo",
)


def construir_consulta(
    q: Optional[str] = None,
    entity_type: Optional[Union[str, List[str]]] = None,
    status: Optional[str] = None,
    owner_user_id: Optional[str] = None,
    filtros: Optional[Dict[str, Any]] = None,
    validity: Optional[str] = None,
) -> Dict[str, Any]:
    """Consulta de Mongo para el listado de entidades del admin."""
    consulta: Dict[str, Any] = {}

    if owner_user_id:
        consulta["owner_user_id"] = owner_user_id

    if entity_type:
        consulta["entity_type"] = (
            {"$in": entity_type} if isinstance(entity_type, list) else entity_type
        )

    if status:
        consulta["status"] = status

    # Validity derived from `valid_until` (datetime at root). It is a COMPUTED
    # facet: there is no stored field, it translates to a condition on the date.
    if validity:
        now = datetime.utcnow()
        if validity == "valid":
            consulta["valid_until"] = {"$gt": now}
        elif validity == "expired":
            consulta["valid_until"] = {"$ne": None, "$lte": now}
        elif validity == "no_expiry":
            consulta["valid_until"] = None

    # Los pickers declaran `filters` como `{"entity_subtype": "..."}`. Se siguen
    # las mismas reglas que `EntityService.find_entities`: una clave sin punto se
    # asume campo de `data`, para no obligar a cada workflow a escribir el
    # prefijo.
    for clave, valor in (filtros or {}).items():
        destino = clave if "." in clave else f"data.{clave}"
        consulta[destino] = {"$in": valor} if isinstance(valor, list) else valor

    texto = (q or "").strip()
    if texto:
        patron = re.escape(texto)
        campos = ["name", "entity_id"] + [f"data.{c}" for c in CAMPOS_BUSCABLES]
        consulta["$or"] = [
            {campo: {"$regex": patron, "$options": "i"}} for campo in campos
        ]

    return consulta


# --- Generic facet explorer --------------------------------------------------
# Hints to auto-detect, within `data.*`, which fields are an ADDRESS (a dict with
# these subfields) or a GEOMETRY (a dict with type/coordinates). This keeps the
# explorer tenant-agnostic: it facets whatever the data carries.
_ADDRESS_HINTS = {"cp", "codigo_postal", "municipio", "estado", "colonia", "calle"}
_GEO_TYPES = {"Point", "Polygon", "MultiPolygon", "MultiPoint", "LineString"}
_ADDRESS_SUBFIELDS = (
    ("municipio", "Municipio"),
    ("estado", "Estado"),
    ("cp", "Código Postal"),
)


def _detect_data_fields(sample: List[Dict[str, Any]]):
    """From a sample of entities, detect address-like and geo fields in `data.*`."""
    addresses, geos = set(), set()
    for ent in sample:
        data = ent.get("data") or {}
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            if not isinstance(value, dict):
                continue
            if value.get("type") in _GEO_TYPES and "coordinates" in value:
                geos.add(key)
            elif set(value.keys()) & _ADDRESS_HINTS:
                addresses.add(key)
    return sorted(addresses), sorted(geos)


async def facets(
    base_query: Dict[str, Any],
    max_values: int = 12,
    detect_sample: int = 300,
) -> List[Dict[str, Any]]:
    """Facet counts over `base_query`, computed in a single `$facet` aggregation.

    Fixed facets: entity type, subtype, status and validity (derived from
    `valid_until`). In addition, for every `data` field that LOOKS like an address
    it facets municipio/estado/cp, and for every geo field it facets has/no
    location. Address/geo fields are AUTO-DETECTED from a sample → generic, not
    coupled to any tenant.

    Returns a list of groups: {key, field, label, type, values:[{value, count}]}
    where `field` is the Mongo path the frontend uses to filter back.
    """
    from app.models.legal_entity import LegalEntity
    col = LegalEntity.get_motor_collection()
    now = datetime.utcnow()

    sample = await col.find(base_query, {"data": 1}).limit(detect_sample).to_list(detect_sample)
    addresses, geos = _detect_data_fields(sample)

    stages: Dict[str, Any] = {
        "entity_type": [{"$group": {"_id": "$entity_type", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": max_values}],
        "entity_subtype": [{"$group": {"_id": "$data.entity_subtype", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": max_values}],
        "status": [{"$group": {"_id": "$status", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": max_values}],
        "validity": [{"$group": {"_id": "$_validity", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}],
    }
    meta = {
        "entity_type": ("Tipo", "entity_type", "enum"),
        "entity_subtype": ("Subtipo", "data.entity_subtype", "enum"),
        "status": ("Estado", "status", "enum"),
        "validity": ("Vigencia", "validity", "validity"),
    }
    order = ["entity_type", "entity_subtype", "status", "validity"]

    for i, path in enumerate(addresses):
        for sub, label in _ADDRESS_SUBFIELDS:
            key = f"addr{i}_{sub}"
            field = f"data.{path}.{sub}"
            stages[key] = [
                {"$match": {field: {"$nin": [None, ""]}}},
                {"$group": {"_id": f"${field}", "n": {"$sum": 1}}},
                {"$sort": {"n": -1}}, {"$limit": max_values},
            ]
            meta[key] = (f"{label} ({path})", field, "address")
            order.append(key)
    for i, path in enumerate(geos):
        key = f"geo{i}"
        field = f"data.{path}"
        stages[key] = [{"$group": {"_id": {"$cond": [{"$ifNull": [f"${field}", False]}, "has_location", "no_location"]}, "n": {"$sum": 1}}}, {"$sort": {"n": -1}}]
        meta[key] = (f"Ubicación ({path})", field, "geo")
        order.append(key)

    validity_expr = {"$switch": {"branches": [
        {"case": {"$eq": [{"$ifNull": ["$valid_until", None]}, None]}, "then": "no_expiry"},
        {"case": {"$gt": ["$valid_until", now]}, "then": "valid"},
    ], "default": "expired"}}

    result = await col.aggregate([
        {"$match": base_query},
        {"$addFields": {"_validity": validity_expr}},
        {"$facet": stages},
    ]).to_list(1)
    raw = result[0] if result else {}

    groups = []
    for key in order:
        label, field, ftype = meta[key]
        values = [
            {"value": g["_id"], "count": g["n"]}
            for g in raw.get(key, []) if g["_id"] not in (None, "")
        ]
        if values:
            groups.append({"key": key, "field": field, "label": label, "type": ftype, "values": values})
    return groups
