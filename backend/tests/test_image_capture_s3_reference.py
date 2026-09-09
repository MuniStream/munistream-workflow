"""
Los operadores de captura de imagen tienen que entender la referencia S3.

Desde que `submit-data` sube los archivos del ciudadano a S3 (`tmp/<instance>/
<task>/<field>/...`) y guarda en el context solo la referencia
`{filename, content_type, size, s3_key, s3_bucket}`, los operadores de captura
seguían esperando el shape viejo `{'base64': ...}`. `extract_image_from_formdata`
devolvía el dict tal cual, `convert_to_bytes` recibía un dict y devolvía None, y
`validate_capture` cortaba con "Invalid image data format".

Efecto: `capture_selfie` y `capture_id_document` fallaban en los tres intentos y
el trámite terminaba en `failed` sin que el ciudadano pudiera hacer nada
(instancia 02a2958b-d596-43d7-ac13-855c40568e2e de `credencial_rnpa` en dev).

La imagen se resuelve desde S3 solo para validarla; en el context se conserva la
referencia, nunca los bytes — el S3UploadOperator hace después el copy de
`tmp/` al destino final.

Necesita el S3 de desarrollo (MinIO) accesible; si no, se salta.
"""

import asyncio
import io
import os
import sys
import uuid

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from PIL import Image

from app.services import s3_storage
from app.workflows.operators.base import TaskStatus
from app.workflows.operators.id_capture_operator import IDCaptureOperator
from app.workflows.operators.selfie_operator import SelfieOperator

INSTANCE_ID = "test-" + uuid.uuid4().hex[:12]


def _jpeg_nitido(width: int = 1280, height: int = 960) -> bytes:
    """JPEG con suficiente detalle para superar el umbral de calidad."""
    import numpy as np

    rng = np.random.default_rng(1234)
    arr = rng.integers(40, 215, size=(height, width, 3), dtype="uint8")
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGB").save(buf, format="JPEG", quality=92)
    return buf.getvalue()


@pytest.fixture(scope="module")
def selfie_ref():
    """Sube un JPEG real a S3 igual que `submit-data` y devuelve la referencia."""
    contenido = _jpeg_nitido()
    try:
        ref = s3_storage.upload_pending_file(
            instance_id=INSTANCE_ID,
            task_id="capture_selfie",
            field_name="selfie_image",
            filename="selfie.jpg",
            content_type="image/jpeg",
            file_content=contenido,
        )
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"S3/MinIO no disponible: {exc}")

    yield ref, contenido

    try:
        s3_storage.delete_object(ref["s3_bucket"], ref["s3_key"])
    except Exception:  # noqa: BLE001
        pass


def _operador() -> SelfieOperator:
    return SelfieOperator(
        task_id="capture_selfie",
        allow_file_upload=True,
        require_face_detection=False,
        require_liveness=False,
        min_quality_score=1,
    )


def test_extract_conserva_la_referencia_s3(selfie_ref):
    """La referencia no se pierde: es lo que el S3UploadOperator necesita."""
    ref, _ = selfie_ref
    image_data, file_metadata = _operador().extract_image_from_formdata(ref)

    assert image_data["s3_key"] == ref["s3_key"]
    assert image_data["s3_bucket"] == ref["s3_bucket"]
    assert file_metadata["filename"] == "selfie.jpg"
    assert file_metadata["content_type"] == "image/jpeg"
    assert file_metadata["file_size"] == ref["size"]


def test_convert_to_bytes_resuelve_la_referencia_s3(selfie_ref):
    """Antes devolvía None con un dict; ahora baja los bytes de S3."""
    ref, contenido = selfie_ref
    assert _operador().convert_to_bytes(ref) == contenido


def test_image_size_bytes_usa_el_tamano_real(selfie_ref):
    """`len(dict)` daba 5; el tamaño tiene que ser el del archivo."""
    ref, contenido = selfie_ref
    assert _operador().image_size_bytes(ref) == len(contenido)


def test_validate_capture_acepta_la_referencia_s3(selfie_ref):
    ref, _ = selfie_ref
    resultado = asyncio.run(_operador().validate_capture(ref, {}))

    assert resultado["valid"] is True, resultado.get("errors")
    assert resultado.get("reason") != "invalid_format"


def test_execute_deja_la_referencia_lista_para_el_s3upload(selfie_ref):
    """El paso completa y `_selfie_image` lleva la referencia, no los bytes."""
    ref, contenido = selfie_ref
    context = {
        "instance_id": INSTANCE_ID,
        "capture_selfie_input": {"selfie_image": ref, "metadata": "{}"},
    }

    resultado = asyncio.run(_operador().execute_async(context))

    assert resultado.status == TaskStatus.CONTINUE, resultado.error
    subida = resultado.data["_selfie_image"]
    assert subida["s3_key"] == ref["s3_key"]
    assert subida["s3_bucket"] == ref["s3_bucket"]
    assert subida["size"] == len(contenido)
    assert "content" not in subida

    capture = resultado.data["selfie_capture"]
    assert capture["size"] == len(contenido)
    assert capture["image_data"]["s3_key"] == ref["s3_key"]


def test_base64_en_linea_sigue_funcionando():
    """El shape viejo (base64 en el context) no se rompe: los operadores tienen
    que seguir aceptándolo mientras existan instancias creadas antes del cambio
    a S3."""
    import base64

    contenido = _jpeg_nitido()
    b64 = base64.b64encode(contenido).decode()
    entrada = {
        "base64": b64,
        "filename": "selfie.jpg",
        "content_type": "image/jpeg",
        "size": len(contenido),
    }
    context = {
        "instance_id": INSTANCE_ID,
        "capture_selfie_input": {"selfie_image": entrada, "metadata": "{}"},
    }

    resultado = asyncio.run(_operador().execute_async(context))

    assert resultado.status == TaskStatus.CONTINUE, resultado.error
    subida = resultado.data["_selfie_image"]
    assert subida["content"] == b64
    assert subida["size"] == len(contenido)
    assert "s3_key" not in subida


def test_cadena_captura_a_s3upload(selfie_ref):
    """Integración real: lo que produce el operador de captura tiene que servirle
    al S3UploadOperator para hacer el copy de `tmp/` al destino final.

    Es el eslabón que rompía el trámite: sin la referencia en `_selfie_image`,
    el upload no encontraba ni bytes ni s3_key.
    """
    from app.workflows.operators.s3_upload import S3UploadOperator

    ref, contenido = selfie_ref
    context = {
        "instance_id": INSTANCE_ID,
        "capture_selfie_input": {"selfie_image": ref, "metadata": "{}"},
    }

    captura = asyncio.run(_operador().execute_async(context))
    assert captura.status == TaskStatus.CONTINUE, captura.error
    context.update(captura.data)

    upload = S3UploadOperator(
        task_id="upload_selfie_s3",
        file_source="_selfie_image",
        s3_prefix="tests/credencial/selfies",
    )
    resultado = asyncio.run(upload.execute_async(context))

    assert resultado.status == TaskStatus.CONTINUE, resultado.error
    resumen = resultado.data["upload_selfie_s3_result"]
    assert resumen["successful_count"] == 1, resumen["failed_files"]
    destino = resumen["s3_keys"][0]
    assert destino.startswith("tests/credencial/selfies/")

    bucket = ref["s3_bucket"]
    assert s3_storage.download_bytes(bucket, destino) == contenido
    s3_storage.delete_object(bucket, destino)


@pytest.fixture(scope="module")
def id_refs():
    """Frente y reverso de una identificación, subidos como los sube el portal."""
    subidos = []
    try:
        for lado in ("document_front", "document_back"):
            contenido = _jpeg_nitido()
            ref = s3_storage.upload_pending_file(
                instance_id=INSTANCE_ID,
                task_id="capture_id_document",
                field_name=lado,
                filename=f"{lado}.jpg",
                content_type="image/jpeg",
                file_content=contenido,
            )
            subidos.append((ref, contenido))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"S3/MinIO no disponible: {exc}")

    yield subidos

    for ref, _ in subidos:
        try:
            s3_storage.delete_object(ref["s3_bucket"], ref["s3_key"])
        except Exception:  # noqa: BLE001
            pass


def test_id_capture_acepta_referencias_s3(id_refs):
    """El mismo fallo pegaba en `capture_id_document`: frente y reverso llegan
    como referencias S3 y las dos salidas tienen que quedar listas para su
    S3UploadOperator."""
    (front_ref, front_bytes), (back_ref, back_bytes) = id_refs

    operador = IDCaptureOperator(
        task_id="capture_id_document",
        allow_file_upload=True,
        detect_photo=False,
        detect_text=False,
        detect_codes=False,
        min_quality_score=1,
    )
    context = {
        "instance_id": INSTANCE_ID,
        "capture_id_document_input": {
            "document_front": front_ref,
            "document_back": back_ref,
            "metadata": "{}",
        },
    }

    resultado = asyncio.run(operador.execute_async(context))

    assert resultado.status == TaskStatus.CONTINUE, resultado.error
    frente = resultado.data["_id_front_image"]
    reverso = resultado.data["_id_back_image"]
    assert frente["s3_key"] == front_ref["s3_key"]
    assert reverso["s3_key"] == back_ref["s3_key"]
    assert frente["size"] == len(front_bytes)
    assert reverso["size"] == len(back_bytes)
    assert "content" not in frente and "content" not in reverso

    provenance = resultado.data["capture_id_document_provenance"]
    assert provenance["image_count"] == 2
    assert provenance["total_size_bytes"] == len(front_bytes) + len(back_bytes)
