"""Agrupa las firmas que el SignerOperator deja sueltas en una entidad.

El operador publica sus resultados como `<task_id>_<atributo>`:
`sign_document_signer`, `sign_document_signature_chain`, etc. Para imprimir el
bloque de firma de un documento oficial hay que volver a juntarlos por tarea.

Catastro lo resolvía buscando el prefijo `firma_`, que funciona porque sus
tareas se llaman `firma_validacion`, `firma_dictamen_tecnico`… Pero amarra la
presentación al nombre de la tarea: la de CONAPESCA se llama `sign_document` y
por eso nunca aparecía firma en sus documentos; y renombrarla dejaría sin firma
a todo lo ya emitido, que conserva el nombre viejo en su `data`.

Aquí se detecta por el CONJUNTO DE ATRIBUTOS, no por el nombre: cualquier tarea
que haya dejado firmante o cadena de firma cuenta como firma, se llame como se
llame.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Atributos que publica el SignerOperator. El orden no importa.
SIGNATURE_ATTRS = (
    "signer",
    "signed_at",
    "submitted_at",
    "signature_valid",
    "algorithm",
    "signature_chain",
    "cert_subject",
)

# Sin alguno de estos no hay evidencia real de firma, solo un paso que registró
# una fecha. Evita que cualquier `<paso>_submitted_at` se cuele como firma.
REQUIRED_EVIDENCE = ("signer", "signature_chain")


def format_signature_chain(chain: Optional[str], group_size: int = 32) -> str:
    """Parte la cadena base64 en grupos legibles, para cotejarla a simple vista."""
    if not chain:
        return ""
    limpia = str(chain).replace("\n", "").replace(" ", "")
    return " ".join(limpia[i:i + group_size] for i in range(0, len(limpia), group_size))


def extract_signatures(data: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Devuelve las firmas de la entidad, ordenadas por fecha de firma.

    Cada firma trae `task_id` y los atributos que el operador haya publicado,
    más `signature_chain_display` con la cadena ya agrupada.
    """
    if not data:
        return []

    encontradas: Dict[str, Dict[str, Any]] = {}
    for clave, valor in data.items():
        if not isinstance(clave, str):
            continue
        for attr in SIGNATURE_ATTRS:
            sufijo = "_" + attr
            if clave.endswith(sufijo) and len(clave) > len(sufijo):
                task_id = clave[: -len(sufijo)]
                encontradas.setdefault(task_id, {"task_id": task_id})[attr] = valor
                break

    firmas = [
        f for f in encontradas.values()
        if any(f.get(attr) for attr in REQUIRED_EVIDENCE)
    ]

    firmas.sort(key=lambda f: str(f.get("signed_at") or f.get("submitted_at") or ""))
    for f in firmas:
        f["signature_chain_display"] = format_signature_chain(f.get("signature_chain"))
    return firmas
