"""Normalizacion de adjuntos a partir del context de una instancia.

Los shapes de este test estan copiados de los tres productores reales:
``s3_storage.upload_pending_file``, ``S3UploadOperator`` y las claves legadas.
"""

from app.services.instance_attachments import (
    collect_instance_attachments,
    find_attachment,
)


class _FakeInstance:
    def __init__(self, context):
        self.context = context


def test_recoge_subida_del_ciudadano():
    """``{task}_input`` guarda la referencia con ``s3_bucket``, no ``bucket``."""
    inst = _FakeInstance({
        "captura_datos_input": {
            "nombre": "Juan",
            "comprobante": {
                "filename": "comprobante.pdf",
                "content_type": "application/pdf",
                "size": 1234,
                "s3_key": "tmp/i-1/captura_datos/comprobante/abc_comprobante.pdf",
                "s3_bucket": "conapesca-uploads",
            },
        },
        "captura_datos_submitted_at": "2026-01-02T03:04:05",
    })

    atts = collect_instance_attachments(inst)

    assert len(atts) == 1
    att = atts[0]
    assert att["filename"] == "comprobante.pdf"
    assert att["content_type"] == "application/pdf"
    assert att["size"] == 1234
    assert att["s3_bucket"] == "conapesca-uploads"
    assert att["task_id"] == "captura_datos"
    assert att["field"] == "comprobante"
    assert att["origin"] == "citizen_upload"
    assert att["uploaded_at"] == "2026-01-02T03:04:05"


def test_recoge_salida_del_operador_s3():
    """``{task}_result.uploaded_files[]`` usa ``bucket``, y hay listas planas."""
    inst = _FakeInstance({
        "subir_docs_result": {
            "uploaded_files": [
                {
                    "success": True,
                    "filename": "acta.pdf",
                    "s3_key": "final/i-1/acta.pdf",
                    "bucket": "conapesca-uploads",
                    "url": "https://presigned.example/acta.pdf?X-Amz-Expires=604800",
                    "size": 999,
                },
            ],
            "failed_files": [],
            "bucket": "conapesca-uploads",
            "s3_keys": ["final/i-1/acta.pdf"],
            "s3_urls": ["https://presigned.example/acta.pdf?X-Amz-Expires=604800"],
        }
    })

    atts = collect_instance_attachments(inst)

    assert len(atts) == 1, atts
    att = atts[0]
    assert att["filename"] == "acta.pdf"
    assert att["s3_key"] == "final/i-1/acta.pdf"
    assert att["task_id"] == "subir_docs"
    assert att["origin"] == "operator_output"
    # La url presignada caduca a los 7 dias: no debe viajar al cliente.
    assert "url" not in att


def test_no_duplica_el_mismo_archivo_referenciado_dos_veces():
    key = "final/i-1/acta.pdf"
    inst = _FakeInstance({
        "subir_docs_input": {
            "doc": {"filename": "acta.pdf", "s3_key": key, "s3_bucket": "b"},
        },
        "subir_docs_result": {
            "uploaded_files": [
                {"success": True, "filename": "acta.pdf", "s3_key": key, "bucket": "b"},
            ],
            "s3_keys": [key],
        },
    })

    atts = collect_instance_attachments(inst)

    assert len(atts) == 1


def test_ignora_los_intentos_fallidos_del_operador():
    inst = _FakeInstance({
        "subir_docs_result": {
            "uploaded_files": [],
            "failed_files": [
                {"success": False, "filename": "malo.pdf", "s3_key": "x/malo.pdf",
                 "bucket": "b", "error": "boom"},
            ],
        }
    })

    assert collect_instance_attachments(inst) == []


def test_recoge_claves_legadas_y_base64_embebido():
    inst = _FakeInstance({
        "paso_viejo_uploaded_files": {
            "ine": {"filename": "ine.jpg", "s3_key": "old/ine.jpg", "s3_bucket": "b"},
        },
        "otro_paso_input": {
            "foto": {"filename": "foto.png", "content_type": "image/png",
                     "base64": "iVBORw0KGgo="},
        },
    })

    atts = collect_instance_attachments(inst)
    por_nombre = {a["filename"]: a for a in atts}

    assert por_nombre["ine.jpg"]["origin"] == "legacy"
    assert por_nombre["ine.jpg"]["task_id"] == "paso_viejo"
    assert por_nombre["foto.png"]["origin"] == "legacy_inline"
    assert por_nombre["foto.png"]["s3_key"] is None
    # Nunca se devuelve el contenido base64 en el listado.
    assert "base64" not in por_nombre["foto.png"]


def test_context_vacio_o_no_dict():
    assert collect_instance_attachments(_FakeInstance({})) == []
    assert collect_instance_attachments(_FakeInstance(None)) == []


def test_find_attachment_solo_encuentra_lo_de_su_instancia():
    a = _FakeInstance({"t_input": {"f": {"filename": "a.pdf", "s3_key": "k/a.pdf",
                                         "s3_bucket": "b"}}})
    b = _FakeInstance({"t_input": {"f": {"filename": "b.pdf", "s3_key": "k/b.pdf",
                                         "s3_bucket": "b"}}})

    id_de_a = collect_instance_attachments(a)[0]["attachment_id"]

    assert find_attachment(a, id_de_a) is not None
    # El adjunto existe en el bucket, pero no pertenece a esta instancia.
    assert find_attachment(b, id_de_a) is None


def test_el_id_es_estable_entre_llamadas():
    ctx = {"t_input": {"f": {"filename": "a.pdf", "s3_key": "k/a.pdf", "s3_bucket": "b"}}}
    primero = collect_instance_attachments(_FakeInstance(dict(ctx)))[0]["attachment_id"]
    segundo = collect_instance_attachments(_FakeInstance(dict(ctx)))[0]["attachment_id"]
    assert primero == segundo


def test_el_embebido_se_oculta_si_ya_hay_copia_en_s3():
    """Los operadores de captura dejan el base64 y luego suben una copia.

    El revisor debe ver un solo archivo, no el mismo escaneo dos veces.
    """
    inst = _FakeInstance({
        "capture_id_input": {
            "document_front": {"filename": "front_177605.jpg", "content_type": "image/jpeg",
                               "base64": "AAAA", "size": 476854},
        },
        "upload_id_front_s3_result": {
            "uploaded_files": [
                {"success": True, "filename": "capture_id_front_abc_20260413.jpg",
                 "s3_key": "conapesca/rnpa/ids/front.jpg", "bucket": "b", "size": 476854},
            ],
        },
    })

    atts = collect_instance_attachments(inst)

    assert len(atts) == 1
    assert atts[0]["origin"] == "operator_output"
    assert atts[0]["s3_key"] == "conapesca/rnpa/ids/front.jpg"


def test_el_embebido_se_conserva_si_no_tiene_copia_en_s3():
    """Perder un adjunto seria peor que mostrarlo duplicado."""
    inst = _FakeInstance({
        "capture_id_input": {
            "document_front": {"filename": "front.jpg", "base64": "AAAA", "size": 111},
        },
        "otro_result": {
            "uploaded_files": [
                {"success": True, "filename": "otro.pdf", "s3_key": "k/otro.pdf",
                 "bucket": "b", "size": 999},
            ],
        },
    })

    atts = collect_instance_attachments(inst)

    assert len(atts) == 2
    assert {a["origin"] for a in atts} == {"legacy_inline", "operator_output"}


def test_la_referencia_temporal_cede_ante_la_definitiva():
    """El operador borra el objeto `tmp/` tras copiarlo: servirlo daría 404."""
    inst = _FakeInstance({
        "collect_transport_data_input": {
            "facturas_file": {"filename": "documento.pdf", "content_type": "application/pdf",
                              "size": 200, "s3_key": "tmp/i-1/collect/facturas/x_documento.pdf",
                              "s3_bucket": "b"},
        },
        "upload_facturas_s3_result": {
            "uploaded_files": [
                {"success": True, "filename": "documento.pdf", "size": 200,
                 "s3_key": "conapesca/tramites/t/facturas/documento.pdf", "bucket": "b"},
            ],
        },
    })

    atts = collect_instance_attachments(inst)

    assert len(atts) == 1
    assert atts[0]["s3_key"].startswith("conapesca/")


def test_la_temporal_se_conserva_si_aun_no_hay_definitiva():
    """Mientras el operador no haya corrido, la referencia temporal es válida."""
    inst = _FakeInstance({
        "collect_transport_data_input": {
            "facturas_file": {"filename": "documento.pdf", "size": 200,
                              "s3_key": "tmp/i-1/collect/facturas/x_documento.pdf",
                              "s3_bucket": "b"},
        },
    })

    atts = collect_instance_attachments(inst)

    assert len(atts) == 1
    assert atts[0]["s3_key"].startswith("tmp/")


def test_no_se_listan_archivos_de_entidades_elegidas_como_requisito():
    """Esos archivos son de la entidad, no del trámite.

    El revisor los ve abriendo la entidad; mezclarlos aquí los etiqueta con un
    paso inexistente y hace parecer que el ciudadano los adjuntó al trámite.
    """
    inst = _FakeInstance({
        "_selected_entities_data": {
            "pescador_rnpa_ids": {
                "credencial_actividad_file": {"filename": "ajeno.pdf", "size": 200,
                                              "s3_key": "otra/entidad/ajeno.pdf", "s3_bucket": "b"},
            },
        },
        "collect_data_input": {
            "propio": {"filename": "propio.pdf", "size": 900,
                       "s3_key": "conapesca/propio.pdf", "s3_bucket": "b"},
        },
    })

    atts = collect_instance_attachments(inst)

    assert [a["filename"] for a in atts] == ["propio.pdf"]


def test_no_se_listan_los_adjuntos_del_tramite_padre():
    inst = _FakeInstance({
        "_parent_context": {
            "algo_input": {"doc": {"filename": "del_padre.pdf", "size": 200,
                                   "s3_key": "conapesca/padre.pdf", "s3_bucket": "b"}},
        },
        "mi_paso_input": {
            "doc": {"filename": "mio.pdf", "size": 300, "s3_key": "conapesca/mio.pdf",
                    "s3_bucket": "b"},
        },
    })

    atts = collect_instance_attachments(inst)

    assert [a["filename"] for a in atts] == ["mio.pdf"]
