"""Una sección legítimamente vacía se explica, no se presenta como rayas.

Un pago exento no captura importe, fecha ni folio: la sección de pago de la
confirmación salía como seis renglones con "—", indistinguible de un paso que
el ciudadano dejó a medias. La nota condicional explica el vacío. Los campos
siguen mostrándose: esconderlos taparía también los que sí deberían traer dato.
"""
from app.workflows.operators.confirmation_operator import ConfirmationOperator

SECCION = {
    "id": "pago",
    "title": "Pago de derechos",
    "source_task_id": "pago_derechos",
    "note": {"when": "pago_no_aplica", "text": "La modalidad elegida no causa pago."},
    "fields": [{"key": "pago_derechos_data.importe_pagado", "label": "Importe pagado"}],
}


def _op(seccion=SECCION):
    return ConfirmationOperator(task_id="confirmacion", summary_sections=[seccion], declarations=[])


def test_la_nota_aparece_cuando_se_cumple_la_condicion():
    seccion = _op()._resolve_sections({"pago_no_aplica": True})[0]
    assert seccion["note"] == "La modalidad elegida no causa pago."


def test_la_nota_no_aparece_cuando_no_se_cumple():
    seccion = _op()._resolve_sections({"pago_no_aplica": False})[0]
    assert seccion["note"] is None


def test_la_nota_no_aparece_si_la_clave_no_esta():
    seccion = _op()._resolve_sections({})[0]
    assert seccion["note"] is None


def test_los_campos_se_siguen_mostrando_con_la_nota():
    seccion = _op()._resolve_sections({"pago_no_aplica": True})[0]
    assert [f["label"] for f in seccion["fields"]] == ["Importe pagado"]
    assert seccion["fields"][0]["value"] is None


def test_una_nota_sin_condicion_siempre_se_muestra():
    s = dict(SECCION, note={"text": "Siempre"})
    assert _op(s)._resolve_sections({})[0]["note"] == "Siempre"


def test_una_seccion_sin_nota_la_deja_vacia():
    s = {k: v for k, v in SECCION.items() if k != "note"}
    assert _op(s)._resolve_sections({})[0]["note"] is None
