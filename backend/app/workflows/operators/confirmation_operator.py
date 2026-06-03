"""
ConfirmationOperator - Resumen de datos capturados + cláusulas TOS + checkboxes obligatorios.

Se inserta típicamente antes de un paso de revisión humana (por ejemplo, validación
jurídica) para que el ciudadano confirme los datos que capturó, lea cláusulas legales
configurables y acepte declaraciones formales antes de comprometer al equipo de revisión.

El resumen se organiza en secciones, cada una asociada a un paso fuente del DAG; el
frontend usa `operator_kind` como hint de renderizado.

Soporta edición vía rewind del DAG: el frontend puede llamar al endpoint de rewind para
regresar a un paso fuente y limpiar el contexto downstream.
"""

import hashlib
from typing import Dict, Any, List, Optional
from datetime import datetime

from .base import BaseOperator, TaskResult, TaskStatus
from ...core.logging_config import get_workflow_logger

logger = get_workflow_logger(__name__)


def _resolve_path(context: Dict[str, Any], path: str) -> Any:
    """
    Resuelve un dot-path contra el contexto. Soporta acceso por índice numérico
    cuando el segmento es un entero (por ejemplo: "uploads.0.filename"). Devuelve
    None si la ruta no resuelve.
    """
    if not path:
        return None
    parts = path.split(".")
    current: Any = context
    for part in parts:
        if isinstance(current, list):
            try:
                idx = int(part)
            except (TypeError, ValueError):
                return None
            if 0 <= idx < len(current):
                current = current[idx]
            else:
                return None
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


class ConfirmationOperator(BaseOperator):
    """
    Operator que muestra un resumen de los datos capturados, texto de términos y
    condiciones, y checkboxes de declaración obligatorios. Continúa el workflow solo
    cuando todas las declaraciones marcadas como `required` están aceptadas.

    El resumen se compone de "secciones" agrupadas por el paso del DAG que produjo
    los datos. Cada sección declara `source_task_id` (para que el frontend pueda
    disparar rewind a ese paso) y `operator_kind` (hint para renderizado).

    Ejemplo:

        ConfirmationOperator(
            task_id="confirmacion_revision",
            name="Confirmación y aceptación de términos",
            group="Confirmación",
            summary_sections=[
                {
                    "id": "datos_titular",
                    "title": "Datos del titular",
                    "source_task_id": "captura_datos",
                    "operator_kind": "user_input",
                    "fields": [
                        {"key": "captura_datos_data.nombre_completo", "label": "Nombre"},
                        {"key": "captura_datos_data.rfc", "label": "RFC"},
                    ],
                    "editable": True,
                },
                {
                    "id": "documentos",
                    "title": "Documentos",
                    "source_task_id": "subir_identificacion",
                    "operator_kind": "s3_upload",
                    "fields": [
                        {"key": "subir_identificacion.s3_url", "label": "Identificación oficial"},
                    ],
                    "editable": True,
                },
            ],
            tos_text="Términos y condiciones del trámite ...",
            declarations=[
                {"id": "data_correct", "text": "Confirmo que los datos son correctos", "required": True},
                {"id": "tos_accepted", "text": "Acepto los términos y condiciones", "required": True},
                {"id": "under_oath", "text": "Declaro bajo protesta de decir verdad", "required": True},
            ],
        )
    """

    def __init__(
        self,
        task_id: str,
        summary_sections: List[Dict[str, Any]],
        declarations: List[Dict[str, Any]],
        tos_text: str = "",
        tos_text_translations: Optional[Dict[str, str]] = None,
        title: str = "Confirmación y aceptación de términos",
        title_translations: Optional[Dict[str, str]] = None,
        description: Optional[str] = None,
        description_translations: Optional[Dict[str, str]] = None,
        **kwargs,
    ):
        """
        Args:
            task_id: Identificador único de la tarea.
            summary_sections: Lista de secciones del resumen. Cada sección es un dict con
                las claves: id, title, source_task_id, operator_kind, fields, editable
                (ver docstring de clase para el formato completo).
            declarations: Lista de declaraciones que el ciudadano debe marcar. Cada elemento
                es un dict con: id (str), text (str), required (bool), text_translations
                (dict opcional locale->texto).
            tos_text: Texto canónico de los términos y condiciones (formato markdown).
            tos_text_translations: Mapa locale->texto para versiones traducidas del TOS.
            title: Título de la pantalla mostrada al ciudadano.
            title_translations: Traducciones del título por locale.
            description: Descripción opcional debajo del título.
            description_translations: Traducciones de la descripción por locale.
        """
        super().__init__(task_id=task_id, **kwargs)
        self.summary_sections = summary_sections or []
        self.declarations = declarations or []
        self.tos_text = tos_text or ""
        self.tos_text_translations = tos_text_translations or {}
        self.title = title
        self.title_translations = title_translations or {}
        self.description = description
        self.description_translations = description_translations or {}

    def execute(self, context: Dict[str, Any]) -> TaskResult:
        input_key = f"{self.task_id}_input"

        if input_key in context:
            return self._process_confirmation(context)

        return self._present_for_confirmation(context)

    def _resolve_sections(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Resuelve los valores de los campos del resumen contra el contexto actual."""
        resolved_sections: List[Dict[str, Any]] = []
        for section in self.summary_sections:
            resolved_fields: List[Dict[str, Any]] = []
            for field in section.get("fields", []) or []:
                key = field.get("key")
                value = _resolve_path(context, key) if key else None
                resolved_fields.append({
                    "key": key,
                    "label": field.get("label"),
                    "label_translations": field.get("label_translations") or {},
                    "format": field.get("format"),
                    "value": value,
                })
            resolved_sections.append({
                "id": section.get("id"),
                "title": section.get("title"),
                "title_translations": section.get("title_translations") or {},
                "source_task_id": section.get("source_task_id"),
                "operator_kind": section.get("operator_kind"),
                "editable": bool(section.get("editable", True)),
                "fields": resolved_fields,
            })
        return resolved_sections

    def _build_form_config(
        self,
        sections: List[Dict[str, Any]],
        missing: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        rewindable_task_ids = [
            section["source_task_id"]
            for section in sections
            if section.get("editable") and section.get("source_task_id")
        ]
        form_config: Dict[str, Any] = {
            "type": "confirmation",
            "current_step_id": self.task_id,
            "title": self.title,
            "title_translations": self.title_translations,
            "description": self.description,
            "description_translations": self.description_translations,
            "summary_sections": sections,
            "tos_text": self.tos_text,
            "tos_text_translations": self.tos_text_translations,
            "tos_hash": self._compute_tos_hash(),
            "declarations": self.declarations,
            "rewindable_task_ids": rewindable_task_ids,
        }
        if missing:
            form_config["missing_declarations"] = missing
        return form_config

    def _present_for_confirmation(self, context: Dict[str, Any]) -> TaskResult:
        sections = self._resolve_sections(context)
        self.state.waiting_for = "confirmation"
        return TaskResult(
            status=TaskStatus.WAITING,
            data={
                "waiting_for": "confirmation",
                "form_config": self._build_form_config(sections),
            },
        )

    def _process_confirmation(self, context: Dict[str, Any]) -> TaskResult:
        input_data = context.get(f"{self.task_id}_input", {}) or {}
        accepted = input_data.get("declarations_accepted") or []
        if not isinstance(accepted, list):
            accepted = []

        accepted_set = {str(d) for d in accepted}
        required_ids = [d.get("id") for d in self.declarations if d.get("required")]
        missing = [d_id for d_id in required_ids if d_id not in accepted_set]

        if missing:
            logger.info(
                "ConfirmationOperator esperando declaraciones faltantes",
                task_id=self.task_id,
                missing=missing,
            )
            sections = self._resolve_sections(context)
            self.state.waiting_for = "confirmation"
            return TaskResult(
                status=TaskStatus.WAITING,
                data={
                    "waiting_for": "confirmation",
                    "form_config": self._build_form_config(sections, missing=missing),
                },
            )

        rewind_count = 0
        try:
            rewind_count = int(context.get(f"_meta_rewind_count_{self.task_id}", 0) or 0)
        except (TypeError, ValueError):
            rewind_count = 0

        result = {
            "confirmed_at": datetime.utcnow().isoformat(),
            "declarations_accepted": sorted(accepted_set),
            "tos_hash": self._compute_tos_hash(),
            "rewind_count": rewind_count,
        }

        logger.info(
            "ConfirmationOperator confirmación completada",
            task_id=self.task_id,
            declarations=len(accepted_set),
            rewind_count=rewind_count,
        )

        # Al avanzar limpiamos `form_config` y `waiting_for` (que tenían el resumen
        # con valores resueltos del entity y del formulario) para evitar que las
        # validaciones downstream vean datos del trámite duplicados o vacíos.
        return TaskResult(
            status=TaskStatus.CONTINUE,
            data={
                f"{self.task_id}_confirmation": result,
                "form_config": None,
                "waiting_for": None,
            },
        )

    def _compute_tos_hash(self) -> str:
        """SHA256 sobre el texto canónico de los TOS más sus traducciones, para trazabilidad legal."""
        canonical = self.tos_text
        for locale in sorted(self.tos_text_translations.keys()):
            canonical += f"\n[{locale}]" + (self.tos_text_translations[locale] or "")
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
