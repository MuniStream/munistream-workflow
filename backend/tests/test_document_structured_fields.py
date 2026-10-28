"""
Rendering of structured entity values in documents: geometries become map
images, addresses split into components and species lists into rows instead of
raw JSON or a Python repr.
"""
import base64
import io

import pytest
from PIL import Image

from app.core.config import settings
from app.services.pdf_generation import static_map
from app.services.pdf_generation.structured_fields import (
    address_parts,
    field_kind,
    parse_structured,
    point_coordinates,
    polygon_vertex_count,
    species_items,
)
from app.services.pdf_generation.template_engine import TemplateEngine

POINT = {"type": "Point", "coordinates": [-106.4214, 23.2084]}
POLYGON = {"type": "Polygon", "coordinates": [[[-106.45, 23.25], [-106.38, 23.25], [-106.36, 23.19], [-106.44, 23.18], [-106.45, 23.25]]]}
ADDRESS = {"calle": "Av. del Mar", "no_ext": "1200", "colonia": "Centro", "cp": "82000",
           "municipio": "Mazatlán", "estado": "Sinaloa", "telefono": "6691234567"}
SPECIES = [
    {"especie": "Robalo blanco — Centropomus undecimalis", "nombre_comun": "Robalo blanco",
     "nombre_cientifico": "Centropomus undecimalis", "clave": "123", "presentacion": "Entero"},
    {"especie": "Chambo — Chaetodipterus zonatus"},
]


def test_json_strings_are_decoded():
    import json
    assert parse_structured(json.dumps(POINT)) == POINT
    assert parse_structured("[not json") == "[not json"
    assert parse_structured("plain") == "plain"


@pytest.mark.parametrize("value,kind", [
    (POINT, "geo_point"),
    (POLYGON, "geo_polygon"),
    ('{"type":"Point","coordinates":[-106.4,23.2]}', "geo_point"),
    (ADDRESS, "address"),
    ({"pais": "Chile", "municipio": "Valparaíso"}, "address"),
    (SPECIES, "species"),
    ([{"arte": "Red"}], "list"),
    ({"a": 1}, "mapping"),
    ("texto", "scalar"),
])
def test_field_kind(value, kind):
    assert field_kind(value) == kind


def test_address_parts_keep_reading_order():
    keys = [k for k, _ in address_parts(ADDRESS)]
    assert keys == ["calle", "no_ext", "colonia", "cp", "municipio", "estado", "telefono"]


def test_species_items_split_legacy_label_and_hide_bookkeeping():
    items = species_items(SPECIES)
    assert items[0]["common"] == "Robalo blanco"
    assert items[0]["scientific"] == "Centropomus undecimalis"
    assert items[0]["extras"] == [("presentacion", "Entero")]
    assert (items[1]["common"], items[1]["scientific"]) == ("Chambo", "Chaetodipterus zonatus")


def test_geometry_helpers():
    assert point_coordinates(POINT) == (23.2084, -106.4214)
    assert polygon_vertex_count(POLYGON) == 4


def test_format_value_never_prints_a_repr():
    engine = TemplateEngine()
    assert engine._format_value(ADDRESS).startswith("Av. del Mar, 1200, Centro")
    assert engine._format_value(SPECIES) == "Robalo blanco (Centropomus undecimalis), Chambo (Chaetodipterus zonatus)"
    assert "{" not in engine._format_value({"tipo": "red", "cantidad": 2})


def test_format_value_keeps_the_labels_of_a_non_address_mapping():
    engine = TemplateEngine()
    resumen = {"num_facturas": 1, "moneda": "MXN", "total_facturado": 158.01}
    assert engine._format_value(resumen) == "Num Facturas: 1; Moneda: MXN; Total Facturado: 158.01"
    por_especie = {"Atún — Thunnus albacares (kg)": 12.5}
    assert engine._format_value(por_especie) == "Atún — Thunnus albacares (kg): 12.5"


def test_fit_view_frames_the_polygon():
    ring = POLYGON["coordinates"][0]
    zoom, cx, cy = static_map.fit_view(ring, static_map.WIDTH, static_map.HEIGHT, single_point=False)
    xs, ys = zip(*(static_map._project(lng, lat, zoom) for lng, lat in ring))
    assert max(xs) - min(xs) <= static_map.WIDTH and max(ys) - min(ys) <= static_map.HEIGHT
    # One zoom deeper would no longer fit inside the padded frame.
    xs2, ys2 = zip(*(static_map._project(lng, lat, zoom + 1) for lng, lat in ring))
    usable_w = static_map.WIDTH * (1 - 2 * static_map.PADDING)
    usable_h = static_map.HEIGHT * (1 - 2 * static_map.PADDING)
    assert max(xs2) - min(xs2) > usable_w or max(ys2) - min(ys2) > usable_h


def _decode(data_url):
    assert data_url.startswith("data:image/jpeg;base64,")
    return Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1]))).convert("RGB")


def _is_shape_color(pixel):
    r, g, b = pixel
    return r > 120 and g < 90 and b < 110


@pytest.mark.asyncio
async def test_point_map_without_tiles_draws_the_pin(monkeypatch):
    monkeypatch.setattr(settings, "MAP_TILES_ENABLED", False)
    image = _decode(await static_map.render_geometry_map(POINT))
    w, h = image.size
    assert (w, h) == (static_map.WIDTH * static_map.SCALE, static_map.HEIGHT * static_map.SCALE)
    # The pin's tip sits on the point, which is centred; its head is above it.
    head = image.getpixel((w // 2, h // 2 - int(9 * static_map.SCALE * 2.4) + 9 * static_map.SCALE // 2 + 4))
    assert _is_shape_color(head)
    assert not _is_shape_color(image.getpixel((10, 10)))


@pytest.mark.asyncio
async def test_polygon_map_without_tiles_draws_the_perimeter(monkeypatch):
    monkeypatch.setattr(settings, "MAP_TILES_ENABLED", False)
    image = _decode(await static_map.render_geometry_map(json_string(POLYGON)))
    w, h = image.size
    shape_pixels = sum(1 for x in range(0, w, 4) for y in range(0, h, 4) if _is_shape_color(image.getpixel((x, y))))
    assert shape_pixels > 50


@pytest.mark.asyncio
async def test_build_field_maps_only_for_geometries(monkeypatch):
    monkeypatch.setattr(settings, "MAP_TILES_ENABLED", False)
    maps = await static_map.build_field_maps({"zona": POLYGON, "domicilio": ADDRESS, "nombre": "x"})
    assert list(maps) == ["zona"]


def json_string(value):
    import json
    return json.dumps(value)
