"""
`submit-data` recibe formularios con archivos dentro de arrays por transporte
JSON+base64 (el multipart no puede expresar un `file` anidado en un item de
array). El base64 crudo en el context lo borra el stripper de blobs >100 KB del
executor ANTES de que corran los operadores S3 downstream, así que los
documentos reales nunca llegaban a S3 (fallo "No valid file content").

`materialize_base64_files` sube cada archivo serializado (`{filename,
content_type, size, base64}`) a `tmp/` igual que el path multipart y deja en su
lugar la referencia `{..., s3_key, s3_bucket}` sin base64. Así el context nunca
guarda base64 y el S3UploadOperator hace el copy al destino final.

Cubre los dos sitios donde `alta_cuenta_predial` pone archivos: el campo
múltiple de nivel superior `escritura_files` y el anidado
`propietarios[].identificacion_files`.
"""

import base64
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.services import s3_storage
from app.workflows.operators.base import TaskStatus


def _fake_upload(instance_id, task_id, field_name, filename, content_type, file_content):
    return {
        "filename": filename,
        "content_type": content_type,
        "size": len(file_content),
        "s3_key": f"tmp/{instance_id}/{task_id}/{field_name}/deadbeef_{filename}",
        "s3_bucket": "test-bucket",
    }


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def test_top_level_multiple_file_list_becomes_refs(monkeypatch):
    monkeypatch.setattr(s3_storage, "upload_pending_file", _fake_upload)
    data = {
        "escritura_files": [
            {"filename": "a.pdf", "content_type": "application/pdf", "size": 8, "base64": _b64(b"%PDF-1.7a")},
            {"filename": "b.pdf", "content_type": "application/pdf", "size": 8, "base64": _b64(b"%PDF-1.7b")},
        ],
        "observaciones": "una nota",
    }

    out = s3_storage.materialize_base64_files(data, "inst1", "captura_documentos")

    assert out["observaciones"] == "una nota"
    assert isinstance(out["escritura_files"], list)
    assert len(out["escritura_files"]) == 2
    for ref in out["escritura_files"]:
        assert ref["s3_bucket"] == "test-bucket"
        assert ref["s3_key"].startswith("tmp/inst1/captura_documentos/escritura_files/")
        assert "base64" not in ref


def test_single_top_level_file_dict_becomes_ref(monkeypatch):
    monkeypatch.setattr(s3_storage, "upload_pending_file", _fake_upload)
    data = {
        "comprobante": {"filename": "c.pdf", "content_type": "application/pdf", "size": 5, "base64": _b64(b"%PDF?")},
    }

    out = s3_storage.materialize_base64_files(data, "inst1", "paso")

    assert "base64" not in out["comprobante"]
    assert out["comprobante"]["s3_key"].startswith("tmp/inst1/paso/comprobante/")


def test_nested_array_file_fields_become_refs(monkeypatch):
    monkeypatch.setattr(s3_storage, "upload_pending_file", _fake_upload)
    data = {
        "propietarios": [
            {
                "nombres": "OSCAR",
                "porcentaje": 100,
                "identificacion_files": [
                    {"filename": "ine.pdf", "content_type": "application/pdf", "size": 8, "base64": _b64(b"%PDF-ine")},
                ],
            },
        ],
    }

    out = s3_storage.materialize_base64_files(data, "inst1", "captura_documentos")

    prop = out["propietarios"][0]
    assert prop["nombres"] == "OSCAR"
    assert prop["porcentaje"] == 100
    files = prop["identificacion_files"]
    assert len(files) == 1
    assert files[0]["s3_bucket"] == "test-bucket"
    assert files[0]["s3_key"].startswith("tmp/inst1/captura_documentos/identificacion_files/")
    assert "base64" not in files[0]


def test_scalars_and_nonfile_dicts_are_untouched(monkeypatch):
    monkeypatch.setattr(s3_storage, "upload_pending_file", _fake_upload)
    data = {
        "texto": "x",
        "numero": 5,
        "domicilio": {"calle": "Reforma", "cp": "06000", "estado": "CDMX"},
        "lista": [1, 2, 3],
        "vacio": None,
    }

    out = s3_storage.materialize_base64_files(data, "i", "t")

    assert out == data


def test_file_dict_without_base64_is_left_as_is(monkeypatch):
    """Una referencia ya materializada (s3_key, sin base64) no se vuelve a subir."""
    monkeypatch.setattr(s3_storage, "upload_pending_file", _fake_upload)
    ref = {"filename": "a.pdf", "content_type": "application/pdf", "size": 8, "s3_key": "tmp/x", "s3_bucket": "b"}
    data = {"escritura_files": [ref]}

    out = s3_storage.materialize_base64_files(data, "i", "t")

    assert out["escritura_files"][0] == ref


# --- Integración real contra S3: la cadena que rompía alta_cuenta_predial ---
# (el payload JSON+base64 del portal tiene que llegar a S3 vía el S3UploadOperator,
# tanto el campo múltiple de nivel superior como el anidado en el array).

import asyncio
import uuid


def _s3_disponible() -> bool:
    try:
        ref = s3_storage.upload_pending_file(
            "probe-" + uuid.uuid4().hex[:8], "probe", "probe", "p.pdf",
            "application/pdf", b"%PDF-1.7 probe",
        )
        s3_storage.delete_object(ref["s3_bucket"], ref["s3_key"])
        return True
    except Exception:
        return False


def test_cadena_submit_json_a_s3upload():
    """El payload base64 del portal, materializado, tiene que subir a S3 tanto
    `escritura_files` (múltiple, nivel superior) como
    `propietarios[].identificacion_files` (anidado)."""
    if not _s3_disponible():
        import pytest
        pytest.skip("S3/MinIO no disponible")

    from app.workflows.operators.s3_upload import S3UploadOperator

    instance_id = "itest-" + uuid.uuid4().hex[:12]
    payload = {
        "escritura_files": [
            {"filename": "escritura1.pdf", "content_type": "application/pdf", "size": 10, "base64": _b64(b"%PDF-1.7 e1")},
            {"filename": "escritura2.pdf", "content_type": "application/pdf", "size": 10, "base64": _b64(b"%PDF-1.7 e2")},
        ],
        "propietarios": [
            {"nombres": "OSCAR", "porcentaje": 100,
             "identificacion_files": [
                 {"filename": "ine.pdf", "content_type": "application/pdf", "size": 9, "base64": _b64(b"%PDF-1.7 i")},
             ]},
        ],
        "observaciones": "sin observaciones",
    }

    materializado = s3_storage.materialize_base64_files(payload, instance_id, "captura_documentos")

    # El context tras el UserInputOperator (CONTINUE deja `<task>_data`).
    context = {"instance_id": instance_id, "captura_documentos_data": materializado}

    escritura = S3UploadOperator(
        task_id="subir_escritura", file_source="escritura_files",
        s3_prefix="tests/catastro/escrituras",
    )
    res_e = asyncio.run(escritura.execute_async(context))
    assert res_e.status == TaskStatus.CONTINUE, res_e.error
    resumen_e = res_e.data["subir_escritura_result"]
    assert resumen_e["successful_count"] == 2, resumen_e["failed_files"]

    identificacion = S3UploadOperator(
        task_id="subir_identificacion", file_source="propietarios[].identificacion_files",
        s3_prefix="tests/catastro/identificacion",
    )
    res_i = asyncio.run(identificacion.execute_async(context))
    assert res_i.status == TaskStatus.CONTINUE, res_i.error
    resumen_i = res_i.data["subir_identificacion_result"]
    assert resumen_i["successful_count"] == 1, resumen_i["failed_files"]
    assert resumen_i["s3_urls_by_group"] and len(resumen_i["s3_urls_by_group"][0]) == 1

    # Limpieza del destino final.
    for resumen in (resumen_e, resumen_i):
        for key in resumen.get("s3_keys", []):
            try:
                s3_storage.delete_object(s3_storage.default_bucket(), key)
            except Exception:
                pass
