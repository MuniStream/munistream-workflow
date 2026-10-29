"""
Entity type catalog: the type decides how its entities are displayed.

Pure tests (no Mongo, no HTTP):

    docker exec backend-conapesca python -m pytest tests/test_entity_type_catalog.py -q
"""
from types import SimpleNamespace

import pytest

from app.services import entity_type_catalog as catalog
from app.services.visualizers.entity_pdf import select_entity_visualizer


@pytest.fixture(autouse=True)
def _empty_catalog():
    catalog.clear()
    yield
    catalog.clear()


def _entity(entity_type, display=None, data=None):
    return SimpleNamespace(entity_type=entity_type, entity_display_config=display, data=data or {})


OFFICIAL = {"default_view": "pdf_report", "visualizer": "tenant_official", "pdf_template": "official"}


def test_type_display_wins_over_what_the_entity_stored():
    catalog.register_types({"permit": {"name": "Permiso", "display": OFFICIAL}}, "test")
    entity = _entity("permit", {"default_view": "card", "preview_fields": ["a"], "oficio_fields": ["x", "y"]})
    cfg = catalog.effective_display_config(entity)
    assert cfg["default_view"] == "pdf_report"
    assert cfg["visualizer"] == "tenant_official"
    # What depends on the entity's own data is kept.
    assert cfg["oficio_fields"] == ["x", "y"]
    assert cfg["preview_fields"] == ["a"]
    assert select_entity_visualizer(entity) == "tenant_official"


def test_type_without_display_keeps_the_stored_config():
    catalog.register_types({"identity": {"name": "Identidad"}}, "test")
    entity = _entity("identity", {"default_view": "card"})
    assert catalog.effective_display_config(entity) == {"default_view": "card"}


def test_unknown_type_falls_back_to_previous_behaviour():
    signed = _entity("other", None, {"signature": {"value": "s"}})
    assert catalog.effective_display_config(signed) == {}
    assert select_entity_visualizer(signed) == "signed_pdf"
    assert select_entity_visualizer(_entity("other")) == "pdf_report"


def test_invalid_specs_are_skipped():
    count = catalog.register_types({"ok": {"name": "Bien"}, "bad": "text", "bad_display": {"display": "card"}}, "t")
    assert count == 1
    assert catalog.get_type("ok")["name"] == "Bien"
    assert catalog.get_type("bad") is None and catalog.get_type("bad_display") is None


def test_catalog_file_with_yaml_anchors(tmp_path):
    path = tmp_path / catalog.CATALOG_FILE
    path.write_text(
        "_official: &official\n"
        "  default_view: pdf_report\n"
        "  visualizer: tenant_official\n"
        "entity_types:\n"
        "  permit:\n"
        "    name: Permiso de pesca\n"
        "    display: *official\n",
        encoding="utf-8",
    )
    assert catalog.load_catalog_file(path, "t") == 1
    assert catalog.get_type("permit")["display"]["visualizer"] == "tenant_official"
    assert catalog.load_catalog_file(tmp_path / "missing.yaml", "t") == 0


class FakeQueryField:
    """Beanie's query fields, enough for `find_one(Entity.field == value)`."""

    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return (self.name, other)


def _fake_entities(monkeypatch, *entities):
    """Make `LegalEntity.find_one` resolve the given entities, by owner."""
    from app.models import legal_entity

    by_id = {e.entity_id: e for e in entities}
    asked = []

    async def find_one(*conds):
        asked.append(conds)
        values = dict(conds)
        entity = by_id.get(values.get("entity_id"))
        if entity and values.get("owner_user_id") == getattr(entity, "owner_user_id", None):
            return entity
        return None

    monkeypatch.setattr(legal_entity.LegalEntity, "entity_id", FakeQueryField("entity_id"), raising=False)
    monkeypatch.setattr(legal_entity.LegalEntity, "owner_user_id", FakeQueryField("owner_user_id"), raising=False)
    monkeypatch.setattr(legal_entity.LegalEntity, "find_one", find_one, raising=False)
    return asked


def _related(entity_id, entity_type, name, owner="o", data=None):
    return SimpleNamespace(entity_id=entity_id, entity_type=entity_type, name=name,
                           owner_user_id=owner, data=data or {})


def test_related_sections_read_the_linked_entity_of_the_same_owner(monkeypatch):
    import asyncio

    catalog.register_types({
        "permit": {"name": "Permiso", "related": [
            {"field": "unit_ids", "title": "Unidad", "fields": ["folio", "name", "empty"], "labels": {"folio": "Folio"}},
        ]},
    }, "t")
    unit = _related("u1", "unit", "SCPP", data={"folio": "0203", "name": "SCPP", "empty": ""})
    asked = _fake_entities(monkeypatch, unit)

    entity = SimpleNamespace(entity_type="permit", owner_user_id="o", data={"unit_ids": ["u1", "missing"]})
    sections = asyncio.run(catalog.related_sections(entity))

    assert sections == [{"title": "Unidad", "entity_id": "u1", "name": "SCPP", "rows": [
        {"key": "folio", "label": "Folio", "value": "0203"},
        {"key": "name", "label": "Name", "value": "SCPP"},
    ]}]
    assert len(asked) == 2
    assert asyncio.run(catalog.related_sections(_entity("other"))) == []


def test_related_sections_carry_the_name_of_the_linked_entity(monkeypatch):
    import asyncio

    catalog.register_types({
        "permit": {"name": "Permiso", "related": [{"field": "unit_ids", "title": "Unidad", "fields": ["folio"]}]},
        "unit": {"name": "Unidad económica"},
    }, "t")
    _fake_entities(monkeypatch, _related("u1", "unit", "SCPP El Dorado", data={"folio": "0203"}))

    entity = SimpleNamespace(entity_type="permit", owner_user_id="o", data={"unit_ids": ["u1"]})
    sections = asyncio.run(catalog.related_sections(entity))

    assert sections[0]["name"] == "SCPP El Dorado"


def test_related_sections_are_derived_from_the_ids_when_the_type_declares_none(monkeypatch):
    """Un tenant sin `related` declarado (o sin catálogo) conserva sus enlaces."""
    import asyncio

    catalog.register_types({"vessel": {"name": "Embarcación"}}, "t")
    _fake_entities(monkeypatch, _related("v1", "vessel", "Lupita II"))

    entity = SimpleNamespace(entity_type="permit", owner_user_id="o",
                             data={"vessel_ids": ["v1"], "folio": "0203"})
    sections = asyncio.run(catalog.related_sections(entity))

    assert sections == [{"title": "Embarcación", "entity_id": "v1", "name": "Lupita II", "rows": []}]


def test_derived_sections_accept_an_id_field_holding_the_whole_entity(monkeypatch):
    """Según el operador que la escriba, una llave `*_ids` trae el objeto entero."""
    import asyncio

    _fake_entities(monkeypatch, _related("v1", "vessel", "Lupita II"))

    entity = SimpleNamespace(entity_type="permit", owner_user_id="o",
                             data={"vessel_ids": [{"entity_id": "v1", "name": "Lupita II"}]})
    sections = asyncio.run(catalog.related_sections(entity))

    # Sin tipo en el catálogo, el título es el tipo de la entidad relacionada.
    assert sections == [{"title": "vessel", "entity_id": "v1", "name": "Lupita II", "rows": []}]


def test_a_declared_related_leaves_the_other_ids_alone(monkeypatch):
    import asyncio

    catalog.register_types({
        "permit": {"name": "Permiso", "related": [{"field": "unit_ids", "title": "Unidad", "fields": ["folio"]}]},
    }, "t")
    _fake_entities(monkeypatch,
                   _related("u1", "unit", "SCPP El Dorado", data={"folio": "0203"}),
                   _related("v1", "vessel", "Lupita II"))

    entity = SimpleNamespace(entity_type="permit", owner_user_id="o",
                             data={"unit_ids": ["u1"], "vessel_ids": ["v1"]})
    sections = asyncio.run(catalog.related_sections(entity))

    assert [s["entity_id"] for s in sections] == ["u1"]


def test_derived_sections_skip_entities_of_another_owner(monkeypatch):
    import asyncio

    _fake_entities(monkeypatch, _related("v1", "vessel", "Lupita II", owner="otra"))

    entity = SimpleNamespace(entity_type="permit", owner_user_id="o", data={"vessel_ids": ["v1"]})

    assert asyncio.run(catalog.related_sections(entity)) == []
