"""Firmas de un documento, tal como las publica el SignerOperator.

El operador declara con `signatures_key` (default `firmas`) dónde deja la lista;
el trámite mapea esa clave a la entidad y las plantillas la leen desde aquí. Un
único lugar convenido, sin adivinar.

Antes hubo un barrido que reconstruía las firmas a partir de las claves sueltas
`<task_id>_<atributo>`. Se eliminó: acoplaba la presentación al nombre de la
tarea y obligaba a heurísticas para no confundir cualquier `<paso>_submitted_at`
con una firma. Los documentos emitidos antes de `signatures_key` no tienen la
clave y por lo tanto no muestran bloque de firma.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def format_signature_chain(chain: Optional[str], group_size: int = 32) -> str:
    """Parte la cadena base64 en grupos legibles, para cotejarla a simple vista."""
    if not chain:
        return ""
    limpia = str(chain).replace("\n", "").replace(" ", "")
    return " ".join(limpia[i:i + group_size] for i in range(0, len(limpia), group_size))


def extract_signatures(
    data: Optional[Dict[str, Any]], key: str = "firmas"
) -> List[Dict[str, Any]]:
    """Devuelve las firmas de la entidad, ordenadas por fecha de firma.

    Cada una se enriquece con `signature_chain_display`: la cadena ya agrupada,
    lista para imprimir.
    """
    if not data:
        return []

    firmas = [f for f in (data.get(key) or []) if isinstance(f, dict)]
    if not firmas:
        return []

    firmas = [dict(f) for f in firmas]
    firmas.sort(key=lambda f: str(f.get("signed_at") or f.get("submitted_at") or ""))
    for f in firmas:
        f["signature_chain_display"] = format_signature_chain(
            f.get("signature_chain") or f.get("signature")
        )
    return firmas
