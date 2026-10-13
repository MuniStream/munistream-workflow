"""
Estado efectivo de una instancia.

Un trámite RECHAZADO no es un fallo técnico: el motor lo cierra como
`completed` con el desenlace `terminal_status="rejected"`. Para el personal, los
filtros y las métricas eso es un rechazo; contarlo como completado infla las
tasas de éxito y esconde los rechazos. Todo lo que muestra o cuenta estados de
instancias debe pasar por aquí.
"""
from collections import Counter
from typing import Any, Dict, Iterable, Optional

RECHAZADO = "rejected"


def estado_efectivo(status: Any, terminal_status: Optional[str] = None) -> str:
    """Estado a mostrar/contar. Acepta (status, terminal_status) o la instancia."""
    if not isinstance(status, str) and hasattr(status, "status"):
        terminal_status = getattr(status, "terminal_status", None)
        status = status.status
    status = getattr(status, "value", status)
    if status == "completed" and terminal_status == RECHAZADO:
        return RECHAZADO
    return status


def filtro_estado(status: str) -> Dict[str, Any]:
    """Condición de Mongo para filtrar por estado efectivo."""
    status = getattr(status, "value", status)
    if status == RECHAZADO:
        return {"status": "completed", "terminal_status": RECHAZADO}
    if status == "completed":
        return {"status": "completed", "terminal_status": {"$ne": RECHAZADO}}
    return {"status": status}


def contar_por_estado(instancias: Iterable[Any]) -> Dict[str, int]:
    return dict(Counter(estado_efectivo(i) for i in instancias))


# Expresión de agregación ($group/$project) equivalente a `estado_efectivo`.
EXPRESION_ESTADO_EFECTIVO = {
    "$cond": [
        {"$and": [{"$eq": ["$status", "completed"]}, {"$eq": ["$terminal_status", RECHAZADO]}]},
        RECHAZADO,
        "$status",
    ]
}
