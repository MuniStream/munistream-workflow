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
