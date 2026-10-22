"""
Classification of structured entity values for document templates.

Entity data mixes plain scalars with structured values captured by the forms:
GeoJSON geometries, addresses and species lists. Older entities may hold them
as JSON strings (the form submit used to stringify objects). These helpers let
templates render each kind properly instead of printing raw JSON or a Python
repr. Labels are left to the templates, so user-facing text stays there.
"""
import json
from typing import Any, Dict, List, Optional, Tuple

GEO_POINT_TYPES = ("Point", "MultiPoint")
GEO_POLYGON_TYPES = ("Polygon", "MultiPolygon")

# Canonical order of the address keys produced by AddressField.
ADDRESS_KEYS = (
    "calle", "no_ext", "no_int", "colonia", "cp", "municipio", "estado", "pais",
    "telefono", "movil", "correo",
)
# Keys that identify a species item; everything else is an extra attribute.
SPECIES_IDENTITY_KEYS = ("especie", "nombre_comun", "nombre_cientifico")
# Bookkeeping keys of species items that are not meaningful in a document.
SPECIES_HIDDEN_KEYS = ("grupo", "acuacultura", "clave", "aprobada", "paso")


def parse_structured(value: Any) -> Any:
    """Return the decoded object when `value` is a JSON object/array string."""
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in ("{", "[") and text[-1:] in ("}", "]"):
            try:
                return json.loads(text)
            except ValueError:
                return value
    return value


def is_geometry(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("type") in GEO_POINT_TYPES + GEO_POLYGON_TYPES
        and isinstance(value.get("coordinates"), list)
    )


def is_address(value: Any) -> bool:
    if not isinstance(value, dict) or "type" in value:
        return False
    return any(value.get(k) not in (None, "") for k in ("calle", "cp", "colonia", "municipio", "estado"))


def is_species_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(it, dict) for it in value)
        and any(any(it.get(k) for k in SPECIES_IDENTITY_KEYS) for it in value)
    )


def field_kind(value: Any) -> str:
    """One of: geo_point, geo_polygon, address, species, list, mapping, scalar."""
    value = parse_structured(value)
    if is_geometry(value):
        return "geo_point" if value["type"] in GEO_POINT_TYPES else "geo_polygon"
    if is_address(value):
        return "address"
    if is_species_list(value):
        return "species"
    if isinstance(value, (list, tuple)):
        return "list"
    if isinstance(value, dict):
        return "mapping"
    return "scalar"


def address_parts(value: Any) -> List[Tuple[str, str]]:
    """Non-empty address components as (key, value), in reading order."""
    value = parse_structured(value)
    if not isinstance(value, dict):
        return []
    parts = [(k, str(value[k]).strip()) for k in ADDRESS_KEYS if value.get(k) not in (None, "")]
    extra = [
        (k, str(v).strip()) for k, v in value.items()
        if k not in ADDRESS_KEYS and isinstance(v, (str, int, float)) and str(v).strip()
    ]
    return [p for p in parts + extra if p[1]]


def _split_species_label(label: str) -> Tuple[str, str]:
    """`"Atún — Thunnus albacares"` -> ("Atún", "Thunnus albacares")."""
    for sep in (" — ", " - ", " – "):
        if sep in label:
            common, scientific = label.split(sep, 1)
            return common.strip(), scientific.strip()
    return label.strip(), ""


def species_items(value: Any) -> List[Dict[str, Any]]:
    """Species as {common, scientific, extras: [(key, value)]}."""
    value = parse_structured(value)
    if not isinstance(value, list):
        return []
    items = []
    for it in value:
        if not isinstance(it, dict):
            continue
        common = str(it.get("nombre_comun") or "").strip()
        scientific = str(it.get("nombre_cientifico") or "").strip()
        if not (common or scientific) and it.get("especie"):
            common, scientific = _split_species_label(str(it["especie"]))
        extras = [
            (k, v) for k, v in it.items()
            if k not in SPECIES_IDENTITY_KEYS and k not in SPECIES_HIDDEN_KEYS
            and not k.startswith("_") and v not in (None, "", [], {})
            and isinstance(v, (str, int, float)) and not isinstance(v, bool)
        ]
        if common or scientific or extras:
            items.append({"common": common, "scientific": scientific, "extras": extras})
    return items


def point_coordinates(value: Any) -> Optional[Tuple[float, float]]:
    """(lat, lng) of a Point, or the first point of a MultiPoint."""
    value = parse_structured(value)
    if not is_geometry(value) or value["type"] not in GEO_POINT_TYPES:
        return None
    coords = value["coordinates"]
    if value["type"] == "MultiPoint":
        coords = coords[0] if coords else None
    try:
        return float(coords[1]), float(coords[0])
    except (TypeError, ValueError, IndexError):
        return None


def polygon_vertex_count(value: Any) -> int:
    """Distinct vertices of the outer ring(s); the closing point is not counted."""
    value = parse_structured(value)
    if not is_geometry(value) or value["type"] not in GEO_POLYGON_TYPES:
        return 0
    polygons = [value["coordinates"]] if value["type"] == "Polygon" else value["coordinates"]
    total = 0
    for polygon in polygons:
        if polygon and polygon[0]:
            ring = polygon[0]
            total += len(ring) - 1 if len(ring) > 1 and ring[0] == ring[-1] else len(ring)
    return total


def register_filters(env) -> None:
    env.filters["parse_structured"] = parse_structured
    env.filters["field_kind"] = field_kind
    env.filters["address_parts"] = address_parts
    env.filters["species_items"] = species_items
    env.filters["point_coordinates"] = point_coordinates
    env.filters["polygon_vertex_count"] = polygon_vertex_count
