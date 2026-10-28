"""
Catalog of entity types declared by the tenant: display name and how each type
is shown.

How an entity is displayed used to live only in each entity's
``entity_display_config``, written once when the entity was created. Every
source of entities (workflows, sync hooks) had to remember to write it, and
changing it meant rewriting stored entities. The catalog makes the type the
authority: a tenant declares, per entity type, its name and its ``display``
(``default_view``, ``visualizer``, ``pdf_template``, ``preview_fields``…), and
that wins over what the entity stored. The entity keeps only what depends on
its own data (``oficio_fields``, ``folio_field``…).

The catalog is a file versioned with the tenant's workflows,
``entity_types.yaml`` at the root of the plugin repository, loaded at startup
by the plugin loader. Nothing is written to the database::

    entity_types:
      concesion_pesca_comercial:
        name: Concesión de pesca comercial
        display:
          default_view: pdf_report
          visualizer: conapesca_documento_oficial
          pdf_template: documento_oficial
"""
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

logger = logging.getLogger(__name__)

CATALOG_FILE = "entity_types.yaml"

_catalog: Dict[str, Dict[str, Any]] = {}


def register_types(types: Dict[str, Dict[str, Any]], source: str) -> int:
    """Add (or replace) entity types. Returns how many were registered."""
    count = 0
    for type_id, spec in (types or {}).items():
        if not isinstance(spec, dict):
            logger.warning("Entity type %s in %s is not a mapping; skipped", type_id, source)
            continue
        display = spec.get("display")
        if display is not None and not isinstance(display, dict):
            logger.warning("Entity type %s in %s has a non-mapping display; skipped", type_id, source)
            continue
        _catalog[str(type_id)] = {
            "type_id": str(type_id),
            "name": spec.get("name") or str(type_id),
            "alias": spec.get("alias"),
            "description": spec.get("description"),
            "icon": spec.get("icon"),
            "color": spec.get("color"),
            "display": dict(display or {}),
            "source": source,
        }
        count += 1
    return count


def load_catalog_file(path: Path, source: str) -> int:
    """Register the types of a catalog file. A missing file registers nothing."""
    if not path.is_file():
        return 0
    with open(path, encoding="utf-8") as fh:
        content = yaml.safe_load(fh) or {}
    types = content.get("entity_types") if isinstance(content, dict) else None
    if not isinstance(types, dict):
        logger.warning("%s has no 'entity_types' mapping; nothing registered", path)
        return 0
    return register_types(types, source)


def get_type(type_id: Optional[str]) -> Optional[Dict[str, Any]]:
    return _catalog.get(type_id or "")


def all_types() -> Dict[str, Dict[str, Any]]:
    return dict(_catalog)


def clear() -> None:
    """Empty the catalog (tests)."""
    _catalog.clear()


def effective_display_config(entity: Any) -> Dict[str, Any]:
    """How an entity is shown: its stored config, overridden by its type's."""
    stored = getattr(entity, "entity_display_config", None) or {}
    spec = get_type(getattr(entity, "entity_type", None))
    return {**stored, **(spec["display"] if spec else {})}
