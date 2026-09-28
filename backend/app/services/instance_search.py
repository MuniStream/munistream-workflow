"""
Construcción de la consulta para buscar trámites en el admin.

El buscador mandaba el texto como `instance_id`, así que solo encontraba por
UUID exacto. Nadie busca un trámite por su UUID: se busca por el nombre del
ciudadano, su correo o el folio.

El problema de fondo es que el nombre no vive en la instancia. El listado lo
muestra porque `enrich_instances` lo resuelve contra la colección de clientes,
pero lo hace *después* de consultar y paginar, así que no servía para filtrar.
Por eso aquí la búsqueda por nombre se resuelve en dos tiempos: fuera se
traducen los nombres a ids de cliente y se inyectan como `user_ids`.

Se miran además las copias que el propio contexto guarda (`customer_name`,
`customer_email`), que es lo que permite encontrar trámites cuyo cliente ya no
existe o quedó desvinculado.

El texto se escapa siempre: sin escapar, un punto casa con cualquier carácter y
una entrada como `(a+)+` degrada la base con una sola petición.
"""

import re
from typing import Any, Dict, List, Optional

# Copias del ciudadano que viven dentro del propio documento de la instancia.
CAMPOS_DE_CONTEXTO = (
    "context.customer_name",
    "context.customer_email",
    "context.parent_customer_email",
)


def construir_consulta(
    q: Optional[str] = None,
    user_ids: Optional[List[str]] = None,
    status: Optional[str] = None,
    workflow_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Consulta de Mongo para el listado de trámites del admin."""
    consulta: Dict[str, Any] = {}

    if workflow_id:
        consulta["workflow_id"] = workflow_id
    if status:
        consulta["status"] = status

    texto = (q or "").strip()
    if not texto:
        return consulta

    patron = re.escape(texto)
    alternativas: List[Dict[str, Any]] = [
        {"instance_id": {"$regex": patron, "$options": "i"}}
    ]
    alternativas += [
        {campo: {"$regex": patron, "$options": "i"}} for campo in CAMPOS_DE_CONTEXTO
    ]
    if user_ids:
        alternativas.append({"user_id": {"$in": user_ids}})

    consulta["$or"] = alternativas
    return consulta
