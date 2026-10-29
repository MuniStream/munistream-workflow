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
from typing import Any, Dict, List, Optional

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
        related = spec.get("related") or []
        if not isinstance(related, list) or not all(isinstance(r, dict) and r.get("field") for r in related):
            logger.warning("Entity type %s in %s has an invalid related list; ignored", type_id, source)
            related = []
        _catalog[str(type_id)] = {
            "type_id": str(type_id),
            "name": spec.get("name") or str(type_id),
            "alias": spec.get("alias"),
            "description": spec.get("description"),
            "icon": spec.get("icon"),
            "color": spec.get("color"),
            "display": dict(display or {}),
            "related": related,
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


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _related_entity_id(value: Any) -> Optional[str]:
    """The id inside an ``*_ids`` item.

    The key name promises identifiers, but depending on the operator that wrote
    it the item is the whole entity.
    """
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        related_id = value.get("entity_id") or value.get("id")
        return str(related_id) if related_id else None
    return None


def _type_name(entity_type: Optional[str]) -> Optional[str]:
    spec = get_type(entity_type)
    return spec["name"] if spec else entity_type


async def related_sections(entity: Any) -> List[Dict[str, Any]]:
    """Sections with the data of the entities this one points to.

    A type declares them under ``related``: ``field`` is the data key holding
    the related entity id (or ids), and ``title``/``fields``/``labels`` say how
    to show it. When the type declares none — or has no entry in the catalog at
    all, which is every type of a tenant without ``entity_types.yaml`` — the
    sections are derived from the entity's ``*_ids`` keys, with no rows: just
    the related entity, named and linkable.

    Only entities of the same owner are read, and a missing one is skipped.
    Returns ``[{"title", "entity_id", "name", "rows": [{"key", "label", "value"}]}]``.
    """
    from ..models.legal_entity import LegalEntity

    data = getattr(entity, "data", None) or {}
    owner = getattr(entity, "owner_user_id", None)

    async def load(related_id: Optional[str]) -> Any:
        if not related_id:
            return None
        return await LegalEntity.find_one(
            LegalEntity.entity_id == related_id,
            LegalEntity.owner_user_id == owner,
        )

    spec = get_type(getattr(entity, "entity_type", None))
    sections: List[Dict[str, Any]] = []

    if spec and spec["related"]:
        for rel in spec["related"]:
            ids = data.get(rel["field"])
            for item in ids if isinstance(ids, list) else [ids]:
                other = await load(_related_entity_id(item))
                if not other:
                    continue
                other_data = other.data or {}
                labels = rel.get("labels") or {}
                keys = rel.get("fields") or list(other_data.keys())
                sections.append({
                    "title": rel.get("title") or _type_name(other.entity_type),
                    "entity_id": other.entity_id,
                    "name": other.name,
                    "rows": [
                        {"key": k, "label": labels.get(k) or k.replace("_", " ").capitalize(), "value": other_data.get(k)}
                        for k in keys if not _empty(other_data.get(k))
                    ],
                })
        return sections

    for key, value in data.items():
        if not key.endswith("_ids"):
            continue
        for item in value if isinstance(value, list) else [value]:
            other = await load(_related_entity_id(item))
            if not other:
                continue
            sections.append({
                "title": _type_name(other.entity_type),
                "entity_id": other.entity_id,
                "name": other.name,
                "rows": [],
            })
    return sections
