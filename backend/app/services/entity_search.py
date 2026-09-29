"""
Construcción de la consulta para buscar entidades desde el admin.

Hasta ahora las entidades solo se consultaban desde el portal ciudadano, siempre
acotadas a su dueño. El personal no tenía forma de encontrar una embarcación, un
permiso o una credencial sin conocer de antemano de quién era.

El texto del usuario se escapa siempre antes de entrar al regex. Sin escapar, un
punto casa con cualquier carácter y la búsqueda devuelve resultados que no
corresponden; y una entrada como `(a+)+` es un regex catastrófico que degrada la
base con una sola petición.

Sobre el rendimiento: un regex sin anclar no usa índice, así que esto recorre la
colección. Con los volúmenes de un tenant —decenas de miles de entidades— es
aceptable y mucho más simple que la alternativa. Si llega a hacer falta, el
siguiente paso es un índice de texto de Mongo sobre `name` y los campos
identificadores, asumiendo que entonces la búsqueda pasa a ser por palabra
completa y deja de encontrar coincidencias parciales.
"""

import re
from typing import Any, Dict, List, Optional, Union

# Campos de `data` donde vive lo que alguien teclearía para buscar: los
# identificadores oficiales de cada tipo de entidad. No se busca en todo `data`
# a propósito — ahí hay imágenes en base64 y estructuras anidadas que no son
# texto buscable y harían la consulta mucho más cara.
CAMPOS_BUSCABLES = (
    "rfc",
    "curp",
    "rnpa",
    "matricula",
    "folio",
    "nombre_completo",
    "razon_social",
    "numero_permiso",
    "numero_titulo",
)


def construir_consulta(
    q: Optional[str] = None,
    entity_type: Optional[Union[str, List[str]]] = None,
    status: Optional[str] = None,
    owner_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Consulta de Mongo para el listado de entidades del admin."""
    consulta: Dict[str, Any] = {}

    if owner_user_id:
        consulta["owner_user_id"] = owner_user_id

    if entity_type:
        consulta["entity_type"] = (
            {"$in": entity_type} if isinstance(entity_type, list) else entity_type
        )

    if status:
        consulta["status"] = status

    texto = (q or "").strip()
    if texto:
        patron = re.escape(texto)
        campos = ["name", "entity_id"] + [f"data.{c}" for c in CAMPOS_BUSCABLES]
        consulta["$or"] = [
            {campo: {"$regex": patron, "$options": "i"}} for campo in campos
        ]

    return consulta
