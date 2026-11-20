"""Performance reports: reporting periods in Mexico City time and the files."""
import io
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.reportes import periodo_mensual, periodo_semanal, render_pdf, render_xlsx


def test_semana_anterior_de_lunes_a_lunes_en_hora_de_mexico():
    # Thursday 2026-11-19 20:00 UTC = 14:00 in Mexico City.
    desde, hasta = periodo_semanal(datetime(2026, 11, 19, 20, 0))
    assert desde == datetime(2026, 11, 9, 6, 0)   # Mon 9 Nov 00:00 CDMX
    assert hasta == datetime(2026, 11, 16, 6, 0)  # Mon 16 Nov 00:00 CDMX


def test_lunes_temprano_en_utc_sigue_siendo_domingo_en_mexico():
    # Mon 03:00 UTC is still Sunday 21:00 in Mexico City.
    desde, hasta = periodo_semanal(datetime(2026, 11, 16, 3, 0))
    assert hasta == datetime(2026, 11, 9, 6, 0)


def test_mes_anterior():
    desde, hasta = periodo_mensual(datetime(2026, 12, 1, 13, 15))
    assert desde == datetime(2026, 11, 1, 6, 0)
    assert hasta == datetime(2026, 12, 1, 6, 0)


def test_mes_anterior_en_enero():
    desde, hasta = periodo_mensual(datetime(2027, 1, 1, 13, 15))
    assert desde == datetime(2026, 12, 1, 6, 0)
    assert hasta == datetime(2027, 1, 1, 6, 0)


DATOS = {
    "desde": datetime(2026, 11, 9, 6), "hasta": datetime(2026, 11, 16, 6), "institucion": "CONAPESCA",
    "resumen": {
        "recibidos": 3, "resueltos": 2, "aprobados": 1, "rechazados": 1, "porcentaje_en_plazo": 100.0,
        "dias_promedio": 4.5, "en_revision": 1, "semaforo": {"verde": 0, "amarillo": 1, "rojo": 0},
        "satisfaccion": None, "opiniones": 0, "solicitudes_atencion": 0, "solicitudes_respondidas": 0,
        "horas_primera_respuesta": None,
    },
    "tramites": [{
        "workflow_id": "w", "nombre": "Permiso de Pesca", "plazo_dias": 21, "recibidos": 3, "resueltos": 2,
        "aprobados": 1, "rechazados": 1, "en_plazo": 2, "abiertos": 1, "vencidos_abiertos": 0,
        "porcentaje_en_plazo": 100.0, "dias_promedio": 4.5, "dias_max": 6,
    }],
    "pasos": [{"workflow": "Validación", "paso": "admision", "ejecuciones": 2, "horas_promedio": 30.0, "horas_p95": 40.0}],
    "en_riesgo": [{"folio": "F-1", "tramite": "Permiso de Pesca", "color": "amarillo", "dias": 15, "plazo": 21,
                   "compromiso": datetime(2026, 11, 30, 6), "paso": "admision"}],
}


def test_pdf_valido():
    pdf = render_pdf(DATOS)
    assert pdf.startswith(b"%PDF") and len(pdf) > 2000


def test_xlsx_con_las_cuatro_hojas():
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(render_xlsx(DATOS)))
    assert wb.sheetnames == ["Resumen", "Por trámite", "Pasos", "En riesgo"]
    assert wb["Por trámite"]["A2"].value == "Permiso de Pesca"
