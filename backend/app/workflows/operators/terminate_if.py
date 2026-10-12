"""
Termina el trámite con un desenlace de negocio cuando se cumple una condición.

Es la contraparte declarativa de `ShortCircuitOperator`: aquél omite una rama,
éste cierra la instancia entera (p. ej. "rejected") con un motivo legible para el
ciudadano. Las rutas usan la misma sintaxis que `data_mapping` (`a.b|c.d`).
"""
from typing import Any, Dict, List, Optional

from .base import BaseOperator, TaskResult, TaskStatus
from .entity_operators import _resolve_context_path


class TerminateIfOperator(BaseOperator):
    """
    Args:
        conditions: lista de `{"path": ruta, "in": [valores]}`; basta con que se
            cumpla UNA para terminar.
        terminal_status: desenlace con que se cierra la instancia (p. ej. "rejected").
        message_paths: rutas candidatas al motivo; gana la primera con texto.
        message_default: motivo si ninguna ruta trae texto.
    """

    def __init__(
        self,
        task_id: str,
        conditions: List[Dict[str, Any]],
        terminal_status: str,
        message_paths: Optional[List[str]] = None,
        message_default: str = "",
        **kwargs,
    ):
        super().__init__(task_id, **kwargs)
        self.conditions = conditions
        self.terminal_status = terminal_status
        self.message_paths = message_paths or []
        self.message_default = message_default

    def _motivo(self, context: Dict[str, Any]) -> str:
        for path in self.message_paths:
            valor = _resolve_context_path(context, path)
            if isinstance(valor, str) and valor.strip():
                return valor.strip()
        return self.message_default

    def execute(self, context: Dict[str, Any]) -> TaskResult:
        for cond in self.conditions:
            valor = _resolve_context_path(context, cond["path"])
            if valor in cond.get("in", []):
                data = {
                    "terminal_status": self.terminal_status,
                    "terminal_message": self._motivo(context),
                    f"{self.task_id}_terminated": True,
                    f"{self.task_id}_condicion": cond["path"],
                }
                self.state.output_data = data
                return TaskResult(status=TaskStatus.TERMINATE, data=data)
        data = {f"{self.task_id}_terminated": False}
        self.state.output_data = data
        return TaskResult(status=TaskStatus.CONTINUE, data=data)
