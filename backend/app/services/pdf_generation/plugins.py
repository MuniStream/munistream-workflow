"""Plugin registries genéricos para extender la generación de PDFs por tenant.

Los tenants registran aquí (al cargar su plugin de workflows):
  - `MetadataInjectorRegistry`: hook async que enriquece `entity_params["data"]`
    antes de persistir la Entity (p. ej. para generar N° de oficio,
    copiar fecha de inicio del trámite, etc.).
  - `EntityContextResolverRegistry`: hook sync que, dada una Entity,
    construye el contexto extra inyectado al template Jinja bajo una key
    (p. ej. `oficio` con los elementos resueltos desde un catálogo YAML).
  - `TemplateDirRegistry`: directorios adicionales donde buscar templates
    Jinja por nombre. Se mezclan con el directorio default del engine
    vía `jinja2.ChoiceLoader`.

El lookup se hace por `catalog_name` (una string que el tenant escoge y que
queda en `entity_display_config.oficio_catalog`). Así los 15 trámites de
catastro comparten `oficio_catalog="catastro"`, y otros tenants registrarán
sus propios nombres sin colisionar.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

MetadataInjector = Callable[[Dict[str, Any], Dict[str, Any]], Awaitable[None]]
"""Signature: async fn(entity_params, context) → None. Muta entity_params in place."""

EntityContextResolver = Callable[[Any], Optional[Dict[str, Any]]]
"""Signature: fn(entity) → dict | None. Retorna contexto a fusionar en template data."""


class _NamedRegistry:
    """Singleton base para registries keyed por string."""

    _lock = threading.Lock()
    _instances: Dict[type, "_NamedRegistry"] = {}

    def __new__(cls):
        if cls not in cls._instances:
            with cls._lock:
                if cls not in cls._instances:
                    inst = super().__new__(cls)
                    inst._items = {}
                    cls._instances[cls] = inst
        return cls._instances[cls]

    _items: Dict[str, Any]

    @classmethod
    def register(cls, name: str, obj: Any) -> None:
        cls()._items[name] = obj

    @classmethod
    def get(cls, name: str) -> Optional[Any]:
        return cls()._items.get(name)

    @classmethod
    def clear(cls) -> None:
        cls()._items.clear()


class MetadataInjectorRegistry(_NamedRegistry):
    """Async hooks `(entity_params, context) → None` keyed por catalog_name."""


class EntityContextResolverRegistry(_NamedRegistry):
    """Hooks sync `(entity) → dict | None` keyed por catalog_name."""


class TemplateDirRegistry:
    """Directorios adicionales de templates Jinja registrados por tenants."""

    _lock = threading.Lock()
    _dirs: List[Path] = []

    @classmethod
    def register(cls, path: Path | str) -> None:
        p = Path(path)
        with cls._lock:
            if p not in cls._dirs:
                cls._dirs.append(p)

    @classmethod
    def dirs(cls) -> List[Path]:
        return list(cls._dirs)

    @classmethod
    def clear(cls) -> None:
        cls._dirs.clear()
