"""
Document interrogation: read the document the person uploaded with DocumentIA
and have them confirm the data it found.

DocumentIA knows every document type (what data it has, how to read it, what
to check); this operator only sends the file, waits for the reading and turns
the answer into the steps the person sees:

1. **Reading.** The file of an earlier step (`file_source`) is sent to
   DocumentIA and the step waits (`waiting_for="document_reading"`) while the
   executor polls the job.
2. **Another document.** If the reading says the document is not the expected
   one, cannot be read, has expired or is older than `max_age_days`, the person
   is asked for another file with the reasons, without ending the procedure.
   After `max_attempts` files the data is taken as it is and the problems are
   kept for staff.
3. **Review.** A form built from the type's schema, filled in with what was
   read, where the person confirms or corrects each value (`review=True`).
4. **Result.** `<task>_data` holds the data the person confirmed, like any
   form, so an entity created afterwards carries it. `_<task>_extraction`
   keeps the whole record: the type, what was read, what was confirmed, which
   fields were corrected and the problems found (map it into the entity with
   `data_mapping={"_<task>_extraction": "_extraction"}`). When the person
   replaced the file, the new one is published under the field name of
   `file_source`, so the upload step that follows stores it instead of the
   first one.

When DocumentIA cannot read the document (it failed or took longer than
`DOCUMENTIA_MAX_WAIT_SECONDS`), the review form is shown empty and the person
types the data. Without DocumentIA configured the step is skipped.

All the state of an instance lives in its context under `_<task>_doc`: one
operator object serves every instance of a workflow.
"""
import asyncio
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from .base import BaseOperator, TaskResult, TaskStatus
from ..polling_strategy import PollingConfig
from ...core.config import settings
from ...services import documentia
from ...services.documentia import DocumentIAError

logger = logging.getLogger(__name__)

SEPARATOR = "__"          # nested fields: domicilio__codigo_postal
DEFAULT_DATE_FIELDS = ("fecha_emision", "periodo_fin", "fecha_expedicion", "fecha")


def _now() -> str:
    return datetime.utcnow().isoformat()


def _seconds_since(iso: Optional[str]) -> float:
    if not iso:
        return 0.0
    return (datetime.utcnow() - datetime.fromisoformat(iso)).total_seconds()


def _nullable(schema: Dict[str, Any]) -> Dict[str, Any]:
    """The non-null branch of `anyOf: [X, null]`, merged with the field's own keys."""
    for option in schema.get("anyOf") or []:
        if option.get("type") != "null":
            return {**{k: v for k, v in schema.items() if k != "anyOf"}, **option}
    return schema


def _label(text: str) -> str:
    return text.replace("_", " ").capitalize()


class DocumentInterrogationOperator(BaseOperator):
    def __init__(
        self,
        task_id: str,
        document_type: str,
        file_source: str,
        review: bool = True,
        max_age_days: Optional[int] = None,
        date_fields: Optional[List[str]] = None,
        required_fields: Optional[List[str]] = None,
        max_attempts: int = 3,
        publish_data: bool = True,
        **kwargs,
    ):
        """
        Args:
            document_type: a DocumentIA type (identificacion, curp,
                comprobante_domicilio, constancia_situacion_fiscal, escritura,
                permiso, factura, ticket).
            file_source: where the file is, as `<task>.<field>`
                (e.g. `collect_document.files`); the field may hold one file or
                several (front and back).
            review: show the person what was read to confirm or correct it.
            max_age_days: refuse a document older than this, by the first date
                found among `date_fields`.
            required_fields: fields (dotted for nested ones, e.g.
                `domicilio.codigo_postal`) the person must fill in the review.
            max_attempts: files the person may upload before the data is taken
                as it is.
            publish_data: publish the confirmed data as `<task>_data`. Turn it
                off when a step reads several documents whose fields would
                collide in the entity, and map `_<task>_extraction.confirmed`
                explicitly instead.
        """
        super().__init__(task_id=task_id, document_type=document_type, file_source=file_source,
                         review=review, max_age_days=max_age_days, date_fields=date_fields,
                         required_fields=required_fields, max_attempts=max_attempts,
                         publish_data=publish_data, **kwargs)
        self.document_type = document_type
        self.source_task, _, self.source_field = file_source.partition(".")
        self.review = review
        self.max_age_days = max_age_days
        self.date_fields = list(date_fields or DEFAULT_DATE_FIELDS)
        self.required_fields = [f.replace(".", SEPARATOR) for f in (required_fields or [])]
        self.max_attempts = max(1, max_attempts)
        self.publish_data = publish_data
        # The form is built at run time from the document type; the empty one
        # marks the step as one the person takes part in.
        self.form_config: Dict[str, Any] = {}

    # --- scheduling ---------------------------------------------------------
    def get_polling_config(self) -> PollingConfig:
        return PollingConfig.polling(settings.DOCUMENTIA_POLL_SECONDS)

    def polling_config_for(self, context: Dict[str, Any]) -> PollingConfig:
        """Poll only while DocumentIA is reading; a form waits for the person."""
        stage = (context.get(f"_{self.task_id}_doc") or {}).get("stage")
        return self.get_polling_config() if stage == "reading" else PollingConfig.event_driven()

    # --- execution ----------------------------------------------------------
    def execute(self, context: Dict[str, Any]) -> TaskResult:
        return asyncio.run(self.execute_async(context))

    async def execute_async(self, context: Dict[str, Any]) -> TaskResult:
        doc = dict(context.get(f"_{self.task_id}_doc") or {})
        submitted = context.get(f"{self.task_id}_submitted_at")
        answer = context.get(f"{self.task_id}_input") if submitted and submitted != doc.get("consumed") else None

        if not doc:
            files = self._source_files(context)
            if not files:
                logger.warning("%s: no file in %s.%s; nothing to read", self.task_id,
                               self.source_task, self.source_field)
                return self._done({"status": "skipped", "reason": "no_file"})
            if not documentia.configured():
                return self._done({"status": "skipped", "reason": "not_configured"})
            return await self._send({"attempts": 0}, files)

        if answer is not None:
            doc["consumed"] = submitted
            if doc.get("stage") == "replace":
                files = self._files_in((answer or {}).get("documento"))
                if not files:
                    return self._replace_form(doc, ["Sube el documento para continuar."])
                doc["replacement"] = files
                doc.pop("sent_at", None)
                return await self._send(doc, files)
            if doc.get("stage") == "review":
                return await self._confirm(doc, answer or {})

        if doc.get("stage") == "reading":
            return await self._poll(doc)
        if doc.get("stage") == "replace":
            return self._replace_form(doc, doc.get("errors") or [])
        return await self._review_form(doc)

    # --- the file -----------------------------------------------------------
    @staticmethod
    def _files_in(value: Any) -> List[Dict[str, Any]]:
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list):
            return [f for f in value if isinstance(f, dict)]
        return []

    def _source_files(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        for key in (f"{self.source_task}_data", f"{self.source_task}_input"):
            data = context.get(key)
            if isinstance(data, dict) and data.get(self.source_field):
                return self._files_in(data[self.source_field])
        return self._files_in(context.get(self.source_field))

    @staticmethod
    def _content(ref: Dict[str, Any]) -> bytes:
        if ref.get("base64"):
            import base64
            return base64.b64decode(ref["base64"])
        from ...services import s3_storage
        bucket = ref.get("s3_bucket") or ref.get("bucket") or s3_storage.default_bucket()
        return s3_storage.download_bytes(bucket, ref["s3_key"])

    # --- DocumentIA ---------------------------------------------------------
    async def _send(self, doc: Dict[str, Any], files: List[Dict[str, Any]]) -> TaskResult:
        doc.update(stage="reading", job_id=None, files=files, result=None,
                   attempts=doc.get("attempts", 0) + 1, sent_at=doc.get("sent_at") or _now())
        try:
            contents = [{"content": await asyncio.to_thread(self._content, f), "filename": f.get("filename"),
                         "content_type": f.get("content_type")} for f in files]
        except Exception as exc:
            logger.error("%s: could not fetch the file: %s", self.task_id, exc)
            return await self._manual(doc, "file_unavailable")
        try:
            doc["job_id"] = await documentia.submit(self.document_type, contents)
            doc["sent_at"] = _now()
        except DocumentIAError as exc:
            if exc.status == 422:      # DocumentIA refuses the file itself (format, size, damaged PDF)
                doc["stage"] = "replace"
                return self._replace_form(doc, [str(exc).split(": ", 1)[-1]])
            if not exc.retryable:
                logger.error("%s: DocumentIA refused the document: %s", self.task_id, exc)
                return await self._manual(doc, "refused")
            logger.warning("%s: DocumentIA unavailable, will retry: %s", self.task_id, exc)
        return self._reading(doc)

    async def _poll(self, doc: Dict[str, Any]) -> TaskResult:
        if _seconds_since(doc.get("sent_at")) > settings.DOCUMENTIA_MAX_WAIT_SECONDS:
            return await self._manual(doc, "timeout")
        if not doc.get("job_id"):
            return await self._send({**doc, "attempts": doc.get("attempts", 1) - 1}, doc.get("files") or [])
        try:
            job = await documentia.job(doc["job_id"])
        except DocumentIAError as exc:
            if exc.status == 404:      # the job was lost: send the document again
                doc["job_id"] = None
            logger.warning("%s: could not read job %s: %s", self.task_id, doc.get("job_id"), exc)
            return self._reading(doc)
        if job.get("estado") in ("encolado", "corriendo"):
            return self._reading(doc)
        if job.get("estado") != "hecho":
            logger.error("%s: DocumentIA job %s failed: %s", self.task_id, doc["job_id"], job.get("error"))
            return await self._manual(doc, "read_failed")

        result = job.get("resultado") or {}
        doc["result"] = result
        errors = [p.get("mensaje") for p in result.get("problemas") or [] if p.get("severidad") == "error"]
        errors += self._age_errors(result.get("datos") or {})
        if errors and doc.get("attempts", 1) < self.max_attempts:
            doc.update(stage="replace", errors=errors)
            return self._replace_form(doc, errors)
        doc["unresolved"] = errors
        if not self.review:
            return self._done(self._extraction(doc, result.get("datos") or {}), doc)
        doc["stage"] = "review"
        return await self._review_form(doc)

    async def _manual(self, doc: Dict[str, Any], reason: str) -> TaskResult:
        """DocumentIA could not read it: the person types the data in the review form."""
        doc.update(stage="review", manual=reason, result=None)
        try:
            await documentia.document_types()
        except DocumentIAError:
            return self._done({"status": "skipped", "reason": reason}, doc)
        if not self.review:
            return self._done({"status": "skipped", "reason": reason}, doc)
        return await self._review_form(doc)

    def _age_errors(self, data: Dict[str, Any]) -> List[str]:
        if not self.max_age_days:
            return []
        for name in self.date_fields:
            try:
                issued = date.fromisoformat(str(data.get(name) or ""))
            except ValueError:
                continue
            age = (date.today() - issued).days
            if age > self.max_age_days:
                return [f"El documento es del {issued.strftime('%d/%m/%Y')}: tiene más de {self.max_age_days} "
                        "días. Sube uno más reciente."]
            return []
        return []

    # --- what the step returns ------------------------------------------------
    def _reading(self, doc: Dict[str, Any]) -> TaskResult:
        return TaskResult(status=TaskStatus.WAITING, data={
            f"_{self.task_id}_doc": doc,
            "waiting_for": "document_reading",
            "message": "Estamos leyendo tu documento. Esto puede tardar unos minutos.",
        })

    def _replace_form(self, doc: Dict[str, Any], errors: List[str]) -> TaskResult:
        doc["stage"] = "replace"
        left = self.max_attempts - doc.get("attempts", 1)
        return TaskResult(status=TaskStatus.WAITING, data={
            f"_{self.task_id}_doc": doc,
            "waiting_for": "user_input",
            "form_config": {
                "title": "Necesitamos otro documento",
                "description": "El documento que subiste no se puede usar. Sube otro archivo"
                               + (f" (te quedan {left} intento{'s' if left != 1 else ''})." if left > 0 else "."),
                "fields": [{"name": "documento", "label": "Documento", "type": "file", "required": True,
                            "multiple": True, "accept": ".pdf,.jpg,.jpeg,.png,.webp"}],
                "validation_errors": errors,
            },
            "validation_errors": errors,
        })

    async def _review_form(self, doc: Dict[str, Any], errors: Optional[List[str]] = None) -> TaskResult:
        try:
            doc_type = (await documentia.document_types())[self.document_type]
        except (DocumentIAError, KeyError) as exc:
            logger.error("%s: no schema for %s: %s", self.task_id, self.document_type, exc)
            return self._done(self._extraction(doc, (doc.get("result") or {}).get("datos") or {}), doc)
        fields = self._fields(doc_type["esquema"])
        read = (doc.get("result") or {}).get("datos") or {}
        notes = [p.get("mensaje") for p in (doc.get("result") or {}).get("problemas") or []
                 if p.get("severidad") != "error"] + (doc.get("unresolved") or [])
        if doc.get("manual"):
            description = (f"No pudimos leer tu {doc_type['nombre'].lower()} automáticamente. "
                           "Escribe sus datos tal como aparecen en el documento.")
        else:
            description = (f"Estos son los datos que leímos de tu {doc_type['nombre'].lower()}. "
                           "Revísalos y corrige lo que no coincida con el documento.")
        if notes:
            description += " Revisa con atención: " + " ".join(notes)
        form = {
            "title": f"Revisa tu {doc_type['nombre'].lower()}",
            "description": description,
            "fields": fields,
            "initial_values": self._flatten(read, fields),
        }
        if errors:
            form["validation_errors"] = errors
        data = {f"_{self.task_id}_doc": doc, "waiting_for": "user_input", "form_config": form}
        if errors:
            data["validation_errors"] = errors
        return TaskResult(status=TaskStatus.WAITING, data=data)

    async def _confirm(self, doc: Dict[str, Any], answer: Dict[str, Any]) -> TaskResult:
        try:
            schema = (await documentia.document_types())[self.document_type]["esquema"]
        except (DocumentIAError, KeyError):
            schema = {"properties": {}}
        confirmed = self._unflatten(answer, schema)
        missing = [name for name in self.required_fields if self._is_empty(self._lookup(confirmed, name))]
        if missing:
            labels = {f["name"]: f["label"] for f in self._fields(schema)}
            return await self._review_form(doc, [f"Falta: {labels.get(n, n)}." for n in missing])
        read = (doc.get("result") or {}).get("datos") or {}
        return self._done(self._extraction(doc, confirmed, read), doc)

    def _extraction(self, doc: Dict[str, Any], confirmed: Dict[str, Any],
                    read: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        result = doc.get("result") or {}
        read = read if read is not None else (result.get("datos") or {})
        return {
            "status": "manual" if doc.get("manual") else "read",
            "type": self.document_type,
            "version": result.get("version"),
            "extracted": read,
            "confirmed": confirmed,
            "corrections": sorted(self._differences(read, confirmed)) if read else [],
            "problems": result.get("problemas") or [],
            "unresolved": doc.get("unresolved") or [],
            "manual_reason": doc.get("manual"),
            "attempts": doc.get("attempts", 0),
            "job_id": doc.get("job_id"),
        }

    def _done(self, extraction: Dict[str, Any], doc: Optional[Dict[str, Any]] = None) -> TaskResult:
        data: Dict[str, Any] = {f"_{self.task_id}_extraction": extraction, f"{self.task_id}_validated": True}
        replacement = (doc or {}).get("replacement")
        if self.publish_data and extraction.get("confirmed"):
            data[f"{self.task_id}_data"] = extraction["confirmed"]
        if replacement:
            # The upload step after this one reads the file by field name; the
            # replacement goes first in its lookup (a top-level context key).
            data[self.source_field] = replacement if len(replacement) > 1 else replacement[0]
        return TaskResult(status=TaskStatus.CONTINUE, data=data)

    # --- schema ↔ form ------------------------------------------------------
    @staticmethod
    def _field(name: str, schema: Dict[str, Any], label: str) -> Dict[str, Any]:
        schema = _nullable(schema)
        field: Dict[str, Any] = {"name": name, "label": label}
        if schema.get("description"):
            field["helpText"] = schema["description"]
        if "enum" in schema:
            field.update(type="select", options=[{"value": v, "label": _label(str(v))} for v in schema["enum"]])
        elif schema.get("type") in ("number", "integer"):
            field["type"] = "number"
        elif schema.get("type") == "boolean":
            field.update(type="select", options=[{"value": "true", "label": "Sí"}, {"value": "false", "label": "No"}])
        elif schema.get("description") == "AAAA-MM-DD":
            field["type"] = "date"
        else:
            field["type"] = "text"
        return field

    def _fields(self, schema: Dict[str, Any]) -> List[Dict[str, Any]]:
        fields = []
        for name, sub in (schema.get("properties") or {}).items():
            sub = _nullable(sub)
            label = sub.get("title") or _label(name)
            if sub.get("type") == "object":
                for child, child_schema in (sub.get("properties") or {}).items():
                    child_label = _nullable(child_schema).get("title") or _label(child)
                    fields.append(self._field(f"{name}{SEPARATOR}{child}", child_schema, f"{label}: {child_label}"))
            elif sub.get("type") == "array":
                items = _nullable(sub.get("items") or {})
                if items.get("type") == "object":
                    fields.append({
                        "name": name, "label": label, "type": "array",
                        "item_fields": [self._field(k, v, _nullable(v).get("title") or _label(k))
                                        for k, v in (items.get("properties") or {}).items()],
                        "add_button_label": "Agregar",
                    })
                else:
                    fields.append({"name": name, "label": label, "type": "textarea",
                                   "helpText": "Uno por renglón"})
            else:
                fields.append(self._field(name, sub, label))
        for field in fields:
            field["required"] = field["name"] in self.required_fields
        return fields

    @staticmethod
    def _flatten(data: Dict[str, Any], fields: List[Dict[str, Any]]) -> Dict[str, Any]:
        values: Dict[str, Any] = {}
        for field in fields:
            parent, _, child = field["name"].partition(SEPARATOR)
            value = (data.get(parent) or {}).get(child) if child else data.get(parent)
            if field["type"] == "textarea" and isinstance(value, list):
                value = "\n".join(str(v) for v in value)
            elif isinstance(value, bool):
                value = "true" if value else "false"
            if value is not None and value != [] and value != "":
                values[field["name"]] = value
        return values

    @classmethod
    def _coerce(cls, value: Any, schema: Dict[str, Any]) -> Any:
        schema = _nullable(schema)
        kind = schema.get("type")
        if isinstance(value, str):
            value = value.strip()
            if value == "":
                return [] if kind == "array" else None
            if kind in ("number", "integer"):
                try:
                    number = float(value.replace(",", ""))
                    return int(number) if kind == "integer" else number
                except ValueError:
                    return value
            if kind == "boolean":
                return value.lower() in ("true", "si", "sí", "1")
            if kind == "array":
                if value.startswith("["):
                    import json
                    try:
                        value = json.loads(value)
                    except ValueError:
                        return [line.strip() for line in value.splitlines() if line.strip()]
                else:
                    return [line.strip() for line in value.splitlines() if line.strip()]
        if kind == "array" and isinstance(value, list):
            items = _nullable(schema.get("items") or {})
            if items.get("type") == "object":
                return [{k: cls._coerce(row.get(k), v) for k, v in (items.get("properties") or {}).items()}
                        for row in value if isinstance(row, dict)]
            return [cls._coerce(v, items) for v in value]
        return value

    @classmethod
    def _unflatten(cls, answer: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for name, sub in (schema.get("properties") or {}).items():
            sub = _nullable(sub)
            if sub.get("type") == "object":
                result[name] = {child: cls._coerce(answer.get(f"{name}{SEPARATOR}{child}"), child_schema)
                                for child, child_schema in (sub.get("properties") or {}).items()}
            else:
                result[name] = cls._coerce(answer.get(name), sub)
        return result

    @staticmethod
    def _lookup(data: Dict[str, Any], name: str) -> Any:
        parent, _, child = name.partition(SEPARATOR)
        return (data.get(parent) or {}).get(child) if child else data.get(parent)

    @staticmethod
    def _is_empty(value: Any) -> bool:
        return value is None or value == "" or value == []

    @classmethod
    def _differences(cls, read: Any, confirmed: Any, path: str = "") -> List[str]:
        if isinstance(read, dict) and isinstance(confirmed, dict):
            found: List[str] = []
            for key in set(read) | set(confirmed):
                found += cls._differences(read.get(key), confirmed.get(key), f"{path}.{key}" if path else key)
            return found
        if cls._is_empty(read) and cls._is_empty(confirmed):
            return []
        if isinstance(read, (int, float)) and isinstance(confirmed, (int, float)):
            return [] if abs(read - confirmed) < 1e-9 else [path]
        return [] if read == confirmed else [path]
