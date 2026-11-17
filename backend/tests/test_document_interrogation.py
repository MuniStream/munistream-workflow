"""DocumentInterrogationOperator: reading a document with DocumentIA, asking for
another one when it does not serve, and the review the person confirms."""
import asyncio
import base64
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import documentia
from app.services.documentia import DocumentIAError
from app.workflows.operators.base import TaskStatus
from app.workflows.operators.document_interrogation import DocumentInterrogationOperator
from app.workflows.polling_strategy import OperatorPollingStrategy

NULLABLE_STR = lambda title, **kw: {"anyOf": [{"type": "string"}, {"type": "null"}], "title": title, **kw}

SCHEMA = {
    "title": "Comprobante de domicilio",
    "properties": {
        "servicio": {"anyOf": [{"enum": ["luz", "agua"], "type": "string"}, {"type": "null"}], "title": "Servicio"},
        "titular": NULLABLE_STR("Titular"),
        "domicilio": {"type": "object", "title": "Domicilio del servicio", "properties": {
            "calle": NULLABLE_STR("Calle"), "codigo_postal": NULLABLE_STR("Código postal")}},
        "fecha_emision": NULLABLE_STR("Fecha de emisión", description="AAAA-MM-DD"),
        "total": {"anyOf": [{"type": "number"}, {"type": "null"}], "title": "Total a pagar"},
        "especies": {"type": "array", "items": {"type": "string"}, "title": "Especies"},
        "regimenes": {"type": "array", "title": "Regímenes", "items": {"type": "object", "properties": {
            "clave": NULLABLE_STR("Clave"), "descripcion": NULLABLE_STR("Régimen")}}},
    },
}
CATALOG = {"comprobante_domicilio": {"tipo": "comprobante_domicilio", "nombre": "Comprobante de domicilio",
                                     "version": "1", "esquema": SCHEMA}}
FILE = {"filename": "recibo.jpg", "content_type": "image/jpeg", "base64": base64.b64encode(b"jpeg").decode()}
NEW_FILE = {"filename": "otro.pdf", "content_type": "application/pdf", "base64": base64.b64encode(b"pdf").decode()}
TODAY = date.today().isoformat()


class FakeDocumentIA:
    def __init__(self):
        self.sent = []
        self.jobs = {}
        self.submit_error = None
        self.ready = True

    def install(self, monkeypatch):
        async def submit(document_type, files):
            if self.submit_error:
                raise self.submit_error
            self.sent.append((document_type, files))
            job_id = f"job-{len(self.sent)}"
            self.jobs.setdefault(job_id, {"estado": "encolado"})
            return job_id

        async def job(job_id):
            return {"job_id": job_id, **self.jobs[job_id]}

        async def document_types():
            return CATALOG

        monkeypatch.setattr(documentia, "configured", lambda: self.ready)
        monkeypatch.setattr(documentia, "submit", submit)
        monkeypatch.setattr(documentia, "job", job)
        monkeypatch.setattr(documentia, "document_types", document_types)

    def finish(self, job_id, datos, problemas=()):
        self.jobs[job_id] = {"estado": "hecho", "resultado": {
            "tipo": "comprobante_domicilio", "version": "1", "datos": datos, "es_este_tipo": True,
            "legible": True, "problemas": list(problemas)}}


@pytest.fixture
def docia(monkeypatch):
    fake = FakeDocumentIA()
    fake.install(monkeypatch)
    return fake


def _op(**kw):
    return DocumentInterrogationOperator(task_id="extract", document_type="comprobante_domicilio",
                                         file_source="collect_document.files", **kw)


class Instance:
    """The context of one instance, merged the way the executor merges outputs."""

    def __init__(self, op):
        self.op = op
        self.context = {"collect_document_data": {"files": FILE}}
        self.submissions = 0

    def run(self):
        result = asyncio.run(self.op.execute_async(self.context))
        self.context.update(result.data or {})
        return result

    def submit(self, answer):
        self.submissions += 1
        self.context["extract_input"] = answer
        self.context["extract_submitted_at"] = f"2026-11-16T10:00:{self.submissions:02d}"
        return self.run()


def test_reads_then_review_then_confirm(docia):
    inst = Instance(_op(required_fields=["titular", "domicilio.codigo_postal"]))

    r = inst.run()
    assert r.status == TaskStatus.WAITING and r.data["waiting_for"] == "document_reading"
    assert docia.sent[0][0] == "comprobante_domicilio"
    assert docia.sent[0][1][0]["content"] == b"jpeg" and docia.sent[0][1][0]["filename"] == "recibo.jpg"
    assert inst.op.polling_config_for(inst.context).strategy == OperatorPollingStrategy.POLLING

    assert inst.run().data["waiting_for"] == "document_reading"      # still queued

    docia.finish("job-1", {"servicio": "luz", "titular": "ANA PÉREZ", "fecha_emision": TODAY, "total": 350.5,
                           "domicilio": {"calle": "REFORMA 1", "codigo_postal": "06700"},
                           "especies": ["CAMARÓN", "PULPO"], "regimenes": [{"clave": "612", "descripcion": "PF"}]},
                 [{"codigo": "rfc_invalido", "severidad": "aviso", "mensaje": "El RFC leído no es válido."}])
    r = inst.run()
    form = r.data["form_config"]
    assert r.data["waiting_for"] == "user_input" and len(docia.sent) == 1
    assert inst.op.polling_config_for(inst.context).strategy == OperatorPollingStrategy.EVENT_DRIVEN
    fields = {f["name"]: f for f in form["fields"]}
    assert fields["servicio"]["type"] == "select" and fields["total"]["type"] == "number"
    assert fields["fecha_emision"]["type"] == "date" and fields["domicilio__codigo_postal"]["required"]
    assert fields["domicilio__calle"]["label"] == "Domicilio del servicio: Calle"
    assert fields["regimenes"]["type"] == "array" and fields["especies"]["type"] == "textarea"
    assert form["initial_values"]["domicilio__calle"] == "REFORMA 1"
    assert form["initial_values"]["especies"] == "CAMARÓN\nPULPO"
    assert "El RFC leído no es válido." in form["description"]

    r = inst.submit({**form["initial_values"], "titular": "ANA PÉREZ LÓPEZ", "total": "350.50",
                     "especies": "CAMARÓN\nPULPO\nJAIBA"})
    assert r.status == TaskStatus.CONTINUE
    extraction = r.data["_extract_extraction"]
    assert extraction["status"] == "read" and extraction["type"] == "comprobante_domicilio"
    assert extraction["confirmed"]["domicilio"] == {"calle": "REFORMA 1", "codigo_postal": "06700"}
    assert extraction["confirmed"]["total"] == 350.5
    assert extraction["confirmed"]["regimenes"] == [{"clave": "612", "descripcion": "PF"}]
    assert extraction["corrections"] == ["especies", "titular"]
    assert extraction["extracted"]["titular"] == "ANA PÉREZ" and extraction["job_id"] == "job-1"
    assert "files" not in r.data                                     # the file was not replaced
    assert r.data["extract_data"] == extraction["confirmed"]         # an entity created later carries it


def test_required_field_left_empty(docia):
    inst = Instance(_op(required_fields=["titular"]))
    inst.run()
    docia.finish("job-1", {"titular": "ANA"})
    inst.run()
    r = inst.submit({"titular": ""})
    assert r.status == TaskStatus.WAITING and r.data["validation_errors"] == ["Falta: Titular."]
    assert inst.submit({"titular": "ANA"}).status == TaskStatus.CONTINUE


def test_wrong_document_asks_for_another_and_publishes_it(docia):
    inst = Instance(_op())
    inst.run()
    docia.jobs["job-1"] = {"estado": "hecho", "resultado": {"datos": {}, "problemas": [
        {"codigo": "no_es_el_tipo", "severidad": "error", "mensaje": "El documento no parece ser: Comprobante."}]}}
    r = inst.run()
    assert r.data["waiting_for"] == "user_input"
    assert r.data["validation_errors"] == ["El documento no parece ser: Comprobante."]
    assert r.data["form_config"]["fields"][0]["type"] == "file"
    assert "te quedan 2 intentos" in r.data["form_config"]["description"]

    assert inst.submit({}).data["validation_errors"] == ["Sube el documento para continuar."]
    r = inst.submit({"documento": NEW_FILE})
    assert r.data["waiting_for"] == "document_reading" and docia.sent[1][1][0]["content"] == b"pdf"

    docia.finish("job-2", {"titular": "ANA", "fecha_emision": TODAY})
    form = inst.run().data["form_config"]
    r = inst.submit(form["initial_values"])
    assert r.status == TaskStatus.CONTINUE
    assert r.data["files"] == NEW_FILE                               # the upload step stores the new file
    assert r.data["_extract_extraction"]["attempts"] == 2


def test_too_old_and_out_of_attempts(docia):
    inst = Instance(_op(max_age_days=90, max_attempts=2))
    old = (date.today() - timedelta(days=200)).isoformat()
    inst.run()
    docia.finish("job-1", {"titular": "ANA", "fecha_emision": old})
    r = inst.run()
    assert "tiene más de 90 días" in r.data["validation_errors"][0]
    inst.submit({"documento": NEW_FILE})
    docia.finish("job-2", {"titular": "ANA", "periodo_fin": None, "fecha_emision": old})
    r = inst.run()
    assert r.data["waiting_for"] == "user_input" and r.data["form_config"]["fields"][0]["name"] != "documento"
    assert "tiene más de 90 días" in r.data["form_config"]["description"]
    r = inst.submit(r.data["form_config"]["initial_values"])
    assert r.data["_extract_extraction"]["unresolved"] and r.status == TaskStatus.CONTINUE


def test_refused_file_asks_for_another(docia):
    docia.submit_error = DocumentIAError("DocumentIA read: Formato no aceptado: envía JPEG, PNG, WEBP o PDF.",
                                         status=422)
    r = Instance(_op()).run()
    assert r.data["validation_errors"] == ["Formato no aceptado: envía JPEG, PNG, WEBP o PDF."]


def test_unavailable_then_sent(docia):
    docia.submit_error = DocumentIAError("down", retryable=True, status=503)
    inst = Instance(_op())
    assert inst.run().data["waiting_for"] == "document_reading" and not docia.sent
    docia.submit_error = None
    assert inst.run().data["waiting_for"] == "document_reading" and len(docia.sent) == 1
    assert inst.context["_extract_doc"]["attempts"] == 1


def test_failed_reading_lets_the_person_type_it(docia):
    inst = Instance(_op())
    inst.run()
    docia.jobs["job-1"] = {"estado": "error", "error": "GPU"}
    r = inst.run()
    assert r.data["form_config"]["initial_values"] == {}
    assert "No pudimos leer" in r.data["form_config"]["description"]
    r = inst.submit({"titular": "ANA"})
    assert r.data["_extract_extraction"]["status"] == "manual"
    assert r.data["_extract_extraction"]["manual_reason"] == "read_failed"


def test_timeout(docia, monkeypatch):
    from app.core.config import settings
    inst = Instance(_op())
    inst.run()
    monkeypatch.setattr(settings, "DOCUMENTIA_MAX_WAIT_SECONDS", -1)
    r = inst.run()
    assert r.data["waiting_for"] == "user_input" and inst.context["_extract_doc"]["manual"] == "timeout"


def test_without_review(docia):
    inst = Instance(_op(review=False))
    inst.run()
    docia.finish("job-1", {"titular": "ANA"})
    r = inst.run()
    assert r.status == TaskStatus.CONTINUE and r.data["_extract_extraction"]["confirmed"]["titular"] == "ANA"


def test_skipped_without_documentia_or_file(docia):
    docia.ready = False
    r = Instance(_op()).run()
    assert r.status == TaskStatus.CONTINUE
    assert r.data["_extract_extraction"] == {"status": "skipped", "reason": "not_configured"}
    assert "extract_data" not in r.data
    docia.ready = True
    inst = Instance(_op())
    inst.context = {}
    assert inst.run().data["_extract_extraction"]["reason"] == "no_file"


def test_file_from_the_uploaded_reference(docia, monkeypatch):
    from app.services import s3_storage
    monkeypatch.setattr(s3_storage, "download_bytes", lambda bucket, key: f"{bucket}/{key}".encode())
    inst = Instance(_op())
    inst.context = {"collect_document_data": {"files": [
        {"filename": "frente.jpg", "s3_key": "tmp/a/frente.jpg", "s3_bucket": "b"},
        {"filename": "reverso.jpg", "s3_key": "tmp/a/reverso.jpg", "s3_bucket": "b"}]}}
    inst.run()
    assert [f["content"] for f in docia.sent[0][1]] == [b"b/tmp/a/frente.jpg", b"b/tmp/a/reverso.jpg"]


def test_parameters_are_recorded():
    op = _op(max_age_days=90)
    assert op.kwargs["document_type"] == "comprobante_domicilio" and op.kwargs["max_age_days"] == 90
    assert op.form_config == {}


def test_entity_created_afterwards_carries_the_confirmed_data():
    from app.workflows.operators.entity_operators import EntityCreationOperator
    op = EntityCreationOperator(task_id="create_entity", entity_type="document", name_source="Comprobante",
                                data_mapping={"_extract_extraction": "_extraction"})
    context = {"user_id": "u1", "collect_document_data": {"files": FILE},
               "extract_data": {"titular": "ANA", "domicilio": {"codigo_postal": "06700"}},
               "_extract_doc": {"stage": "review"},
               "_extract_extraction": {"status": "read", "corrections": ["titular"]}}
    op.execute(context)
    params = op._entity_params
    assert params["data"]["titular"] == "ANA" and params["data"]["domicilio"] == {"codigo_postal": "06700"}
    assert params["data"]["_extraction"]["corrections"] == ["titular"]
    assert "stage" not in params["data"]
    assert "_extraction" not in params["entity_display_config"]["oficio_fields"]


def test_data_not_published_when_asked(docia):
    inst = Instance(_op(review=False, publish_data=False))
    inst.run()
    docia.finish("job-1", {"titular": "ANA"})
    r = inst.run()
    assert "extract_data" not in r.data and r.data["_extract_extraction"]["confirmed"]["titular"] == "ANA"


def test_replacement_published_without_review(docia):
    inst = Instance(_op(review=False))
    inst.run()
    docia.jobs["job-1"] = {"estado": "hecho", "resultado": {"datos": {}, "problemas": [
        {"codigo": "no_es_el_tipo", "severidad": "error", "mensaje": "El documento no parece ser: Comprobante."}]}}
    inst.run()
    inst.submit({"documento": NEW_FILE})
    docia.finish("job-2", {"titular": "ANA"})
    r = inst.run()
    assert r.status == TaskStatus.CONTINUE
    assert r.data["files"] == NEW_FILE                               # the upload step stores the new file


def test_replacement_published_when_it_cannot_be_read_either(docia):
    inst = Instance(_op(review=False))
    inst.run()
    docia.jobs["job-1"] = {"estado": "hecho", "resultado": {"datos": {}, "problemas": [
        {"codigo": "no_es_el_tipo", "severidad": "error", "mensaje": "El documento no parece ser: Comprobante."}]}}
    inst.run()
    inst.submit({"documento": NEW_FILE})
    docia.jobs["job-2"] = {"estado": "error", "error": "GPU"}
    r = inst.run()
    assert r.status == TaskStatus.CONTINUE and r.data["_extract_extraction"]["status"] == "skipped"
    assert r.data["files"] == NEW_FILE
