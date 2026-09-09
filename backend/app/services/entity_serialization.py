"""Serializacion de entidades para respuestas de API.

Las entidades legadas embeben imagenes y PDFs en base64 dentro de ``data``, de
varios MB cada uno. Devolver ese ``data`` entero convierte el listado de la
cartera de un ciudadano en decenas de MB de JSON. Aqui viven las dos formas de
recortarlo:

- ``slim_entity_data``: para listados. Descarta cualquier valor que pase de un
  umbral pequeno; la UI de lista solo pinta campos de texto cortos.
- ``describe_blobs``: para el detalle. Conserva la estructura pero sustituye los
  valores pesados por un descriptor, de modo que la UI sepa que el campo existe
  y pueda pedir su contenido aparte en vez de recibirlo siempre.
"""

import json
from typing import Any, Dict

# Umbral por defecto de un valor "ligero". Un campo de texto normal cabe de
# sobra; una imagen en base64 no.
DEFAULT_MAX_BYTES = 1024

# Umbral del detalle: mas generoso que el del listado, porque ahi si queremos
# ver los campos estructurados completos, pero no los blobs.
DETAIL_MAX_BYTES = 4096


def _approx_size(value: Any) -> int:
    try:
        return len(value) if isinstance(value, str) else len(json.dumps(value, default=str))
    except (TypeError, ValueError):
        # Si no se puede medir, se trata como pesado: mas vale recortar de mas.
        return DEFAULT_MAX_BYTES + 1


def slim_entity_data(data: Dict[str, Any], max_bytes: int = DEFAULT_MAX_BYTES) -> Dict[str, Any]:
    """Quita los valores pesados del ``data`` de una entidad, para vistas de lista.

    El detalle completo sigue disponible por el endpoint de detalle.
    """
    if not data:
        return {}
    return {k: v for k, v in data.items() if _approx_size(v) <= max_bytes}


def describe_blobs(value: Any, max_bytes: int = DETAIL_MAX_BYTES, _depth: int = 0) -> Any:
    """Sustituye los valores pesados por un descriptor, conservando la estructura.

    Devuelve ``{"__blob": True, "size": ..., "preview": ...}`` en lugar del
    contenido, para que el cliente pueda decidir si lo pide.
    """
    if _depth > 8:
        return {"__truncated": True}

    if isinstance(value, dict):
        return {k: describe_blobs(v, max_bytes, _depth + 1) for k, v in value.items()}

    if isinstance(value, list):
        return [describe_blobs(v, max_bytes, _depth + 1) for v in value]

    if _approx_size(value) > max_bytes:
        return {
            "__blob": True,
            "size": _approx_size(value),
            "preview": value[:64] + "..." if isinstance(value, str) else None,
        }

    return value
