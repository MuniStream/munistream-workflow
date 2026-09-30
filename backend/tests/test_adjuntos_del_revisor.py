"""
El revisor no podía adjuntar nada al expediente.

De los adjuntos hay tres endpoints y todos son de lectura: listar, descargar con
verificación de pertenencia, y emitir un token de descarga corto. La única
escritura del trámite es `submit-data`, que exige un paso en espera — eso es
"contestar el formulario", no "agregar un documento al expediente".

Así que cuando el revisor recibe algo por fuera —un oficio de otra dependencia, el
acuse de una notificación, una constancia que el ciudadano llevó en papel— no
tiene dónde ponerlo. Queda en su correo, y el expediente miente por omisión.

**Dónde viven.** En un campo propio de la instancia, no dentro de `context`. El
contexto es *dato del trámite*: lo recorren los `data_mapping`, acaba en los
documentos emitidos y se copia en cada `pre_task_context_snapshots`. Un oficio que
el revisor archiva no es una respuesta del ciudadano, y meterlo ahí lo colaría en
los documentos que el sistema emite.

**Por qué salen juntos de todos modos.** Quien abre el expediente quiere *los*
documentos del caso, no dos listas que tiene que cruzar. Se unifican al leer, con
el `origin` diciendo de dónde viene cada uno.

Son pruebas puras: no tocan Mongo ni S3.
"""

import os
import sys
from types import SimpleNamespace

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.services.instance_attachments import (
    ORIGEN_REVISOR,
    collect_instance_attachments,
    registro_de_adjunto_del_revisor,
)


def _instancia(context=None, staff=None):
    return SimpleNamespace(
        instance_id="i-1",
        context=context if context is not None else {},
        staff_attachments=staff if staff is not None else [],
    )


SUBIDA_DEL_CIUDADANO = {
    "collect_datos_data": {
        "identificacion_file": {
            "s3_key": "conapesca/tramites/x/ine.pdf",
            "s3_bucket": "munistream",
            "filename": "ine.pdf",
            "size": 1024,
        }
    }
}


# ---------------------------------------------------------------------------
# El registro que se guarda
# ---------------------------------------------------------------------------

def test_el_registro_lleva_lo_que_identifica_al_archivo():
    reg = registro_de_adjunto_del_revisor(
        s3_key="conapesca/expediente/i-1/oficio.pdf",
        s3_bucket="munistream",
        filename="oficio.pdf",
        content_type="application/pdf",
        size=2048,
        uploaded_by="revisor@conapesca.gob.mx",
        uploaded_at="2026-09-30T12:00:00",
    )

    assert reg["s3_key"] == "conapesca/expediente/i-1/oficio.pdf"
    assert reg["filename"] == "oficio.pdf"
    assert reg["size"] == 2048
    assert reg["origin"] == ORIGEN_REVISOR


def test_el_registro_dice_quien_lo_subio():
    """
    Un documento que aparece en el expediente sin saber quién lo puso es peor que
    no tenerlo: no se puede contrastar ni pedir cuentas.
    """
    reg = registro_de_adjunto_del_revisor(
        s3_key="k", s3_bucket="b", filename="f.pdf", content_type="application/pdf",
        size=1, uploaded_by="revisor@conapesca.gob.mx", uploaded_at="2026-09-30T12:00:00",
    )

    assert reg["uploaded_by"] == "revisor@conapesca.gob.mx"
    assert reg["uploaded_at"] == "2026-09-30T12:00:00"


def test_el_identificador_es_el_mismo_que_para_los_demas_adjuntos():
    """
    La descarga y el token se resuelven por `attachment_id`. Si el del revisor se
    calculara distinto, el archivo aparecería en la lista y no se podría abrir.
    """
    from app.services.instance_attachments import _attachment_id

    reg = registro_de_adjunto_del_revisor(
        s3_key="conapesca/expediente/i-1/oficio.pdf", s3_bucket="munistream",
        filename="oficio.pdf", content_type="application/pdf", size=1,
        uploaded_by="r@x", uploaded_at="2026-09-30T12:00:00",
    )

    assert reg["attachment_id"] == _attachment_id("munistream", "conapesca/expediente/i-1/oficio.pdf")


# ---------------------------------------------------------------------------
# Se leen junto con los demás
# ---------------------------------------------------------------------------

def test_los_del_revisor_salen_en_la_lista_del_expediente():
    reg = registro_de_adjunto_del_revisor(
        s3_key="conapesca/expediente/i-1/oficio.pdf", s3_bucket="munistream",
        filename="oficio.pdf", content_type="application/pdf", size=1,
        uploaded_by="r@x", uploaded_at="2026-09-30T12:00:00",
    )

    adjuntos = collect_instance_attachments(_instancia(staff=[reg]))

    assert [a["filename"] for a in adjuntos] == ["oficio.pdf"]


def test_conviven_con_los_del_ciudadano():
    reg = registro_de_adjunto_del_revisor(
        s3_key="conapesca/expediente/i-1/oficio.pdf", s3_bucket="munistream",
        filename="oficio.pdf", content_type="application/pdf", size=1,
        uploaded_by="r@x", uploaded_at="2026-09-30T12:00:00",
    )

    adjuntos = collect_instance_attachments(_instancia(context=SUBIDA_DEL_CIUDADANO, staff=[reg]))

    assert sorted(a["filename"] for a in adjuntos) == ["ine.pdf", "oficio.pdf"]


def test_se_distingue_de_donde_viene_cada_uno():
    """
    Quien revisa necesita saber qué aportó el ciudadano y qué se archivó después;
    no valen lo mismo para resolver.
    """
    reg = registro_de_adjunto_del_revisor(
        s3_key="conapesca/expediente/i-1/oficio.pdf", s3_bucket="munistream",
        filename="oficio.pdf", content_type="application/pdf", size=1,
        uploaded_by="r@x", uploaded_at="2026-09-30T12:00:00",
    )

    por_nombre = {a["filename"]: a for a in
                  collect_instance_attachments(_instancia(context=SUBIDA_DEL_CIUDADANO, staff=[reg]))}

    assert por_nombre["oficio.pdf"]["origin"] == ORIGEN_REVISOR
    assert por_nombre["ine.pdf"]["origin"] != ORIGEN_REVISOR


def test_una_instancia_sin_adjuntos_del_revisor_sigue_igual():
    """El campo es nuevo; las instancias viejas no lo traen."""
    adjuntos = collect_instance_attachments(SimpleNamespace(context=SUBIDA_DEL_CIUDADANO))

    assert [a["filename"] for a in adjuntos] == ["ine.pdf"]


def test_el_mismo_archivo_no_se_duplica():
    """
    Si el revisor archiva un documento que ya estaba en el contexto, la lista debe
    enseñarlo una vez: el expediente se lee para contar documentos, no copias.
    """
    reg = registro_de_adjunto_del_revisor(
        s3_key="conapesca/tramites/x/ine.pdf", s3_bucket="munistream",
        filename="ine.pdf", content_type="application/pdf", size=1024,
        uploaded_by="r@x", uploaded_at="2026-09-30T12:00:00",
    )

    adjuntos = collect_instance_attachments(_instancia(context=SUBIDA_DEL_CIUDADANO, staff=[reg]))

    assert len(adjuntos) == 1


def test_un_registro_corrupto_no_tumba_el_expediente():
    """
    El expediente se abre para trabajar; que un registro mal formado impida verlo
    entero es peor que omitir ese registro.
    """
    adjuntos = collect_instance_attachments(
        _instancia(context=SUBIDA_DEL_CIUDADANO, staff=[{"sin": "s3_key"}, None, "texto"])
    )

    assert [a["filename"] for a in adjuntos] == ["ine.pdf"]
