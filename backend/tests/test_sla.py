"""Semáforo híbrido: plazo legal de resolución (días hábiles desde que el trámite
entra a revisión) y permanencia en el paso actual frente a su histórico."""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.sla import (
    parse_plazo,
    contar_dias_habiles,
    color_plazo,
    color_permanencia,
    peor_color,
    evaluar_plazo,
    _p95,
)

LUNES = datetime(2026, 11, 16, 10, 0)  # lunes


# --- parse_plazo -------------------------------------------------------------

def test_plazo_en_dias_habiles():
    assert parse_plazo("21 días hábiles") == (21, True)


def test_plazo_sin_acentos_y_naturales():
    assert parse_plazo("10 dias habiles") == (10, True)
    assert parse_plazo("30 días naturales") == (30, False)


def test_textos_que_no_son_plazo_de_resolucion():
    # Plazos de presentación o resoluciones inmediatas no definen un semáforo.
    assert parse_plazo("Inmediato") is None
    assert parse_plazo("Inmediata (días y horas hábiles)") is None
    assert parse_plazo("Presentar dentro de 72 horas posteriores al arribo") is None
    assert parse_plazo("Presentar dentro de 5 días hábiles posteriores a la siembra") is None
    assert parse_plazo(None) is None
    assert parse_plazo("") is None


def test_plazo_estructurado_tiene_prioridad():
    assert parse_plazo({"dias": 15, "habiles": False}) == (15, False)
    assert parse_plazo({"dias": 0}) is None


# --- contar_dias_habiles -----------------------------------------------------

def test_mismo_dia_es_cero():
    assert contar_dias_habiles(LUNES, LUNES + timedelta(hours=5)) == 0


def test_una_semana_son_cinco_dias_habiles():
    assert contar_dias_habiles(LUNES, LUNES + timedelta(days=7)) == 5


def test_fin_de_semana_no_cuenta():
    viernes = LUNES + timedelta(days=4)
    assert contar_dias_habiles(viernes, viernes + timedelta(days=3)) == 1  # al lunes


def test_naturales_cuenta_todos():
    assert contar_dias_habiles(LUNES, LUNES + timedelta(days=7), habiles=False) == 7


# --- colores -----------------------------------------------------------------

def test_color_plazo_por_avance():
    assert color_plazo(0.0) == "verde"
    assert color_plazo(0.69) == "verde"
    assert color_plazo(0.7) == "amarillo"
    assert color_plazo(1.0) == "amarillo"
    assert color_plazo(1.01) == "rojo"


def test_umbral_amarillo_configurable():
    assert color_plazo(0.6, umbral_amarillo=0.5) == "amarillo"


def test_color_permanencia_contra_p95():
    assert color_permanencia(10, 20) == "verde"
    assert color_permanencia(30, 20) == "amarillo"
    assert color_permanencia(41, 20) == "rojo"
    assert color_permanencia(10, None) is None  # sin histórico no se juzga


def test_peor_color():
    assert peor_color("verde", "amarillo") == "amarillo"
    assert peor_color("rojo", "verde") == "rojo"
    assert peor_color(None, "verde") == "verde"
    assert peor_color(None, None) is None


# --- evaluar_plazo -----------------------------------------------------------

def test_plazo_en_curso():
    r = evaluar_plazo(LUNES, (21, True), ahora=LUNES + timedelta(days=7))
    assert r["dias_transcurridos"] == 5
    assert r["fecha_compromiso"] == datetime(2026, 12, 15, 10, 0)
    assert r["color"] == "verde"
    assert r["pausado"] is False


def test_plazo_vencido():
    r = evaluar_plazo(LUNES, (5, True), ahora=LUNES + timedelta(days=14))
    assert r["dias_transcurridos"] == 10
    assert r["color"] == "rojo"


def test_pausa_congela_el_reloj():
    # El revisor pidió correcciones al ciudadano el miércoles: desde ahí no corre.
    miercoles = LUNES + timedelta(days=2)
    r = evaluar_plazo(LUNES, (5, True), ahora=LUNES + timedelta(days=30), pausado_desde=miercoles)
    assert r["dias_transcurridos"] == 2
    assert r["pausado"] is True
    assert r["color"] == "verde"


def test_resuelto_usa_fecha_de_resolucion():
    r = evaluar_plazo(LUNES, (5, True), ahora=LUNES + timedelta(days=60), resuelto_en=LUNES + timedelta(days=3))
    assert r["dias_transcurridos"] == 3
    assert r["en_plazo"] is True


# --- _p95 --------------------------------------------------------------------

def test_p95_exige_muestra_minima():
    assert _p95([1, 2, 3, 4]) is None
    assert _p95([1, 2, 3, 4, 100]) == 100
    assert _p95(list(range(1, 101))) == 95


def test_dias_se_cuentan_en_calendario_de_mexico():
    # Submitted Mon 21:00 in Mexico City (Tue 03:00 UTC); by Tue 14:00 local
    # one business day has passed, though both instants are Tuesday in UTC.
    presentado = datetime(2026, 11, 17, 3, 0)
    r = evaluar_plazo(presentado, (21, True), ahora=datetime(2026, 11, 17, 20, 0))
    assert r["dias_transcurridos"] == 1
    # And the due date is 21 business days after Monday 16 Nov (local).
    assert (r["fecha_compromiso"] - timedelta(hours=6)).date() == datetime(2026, 12, 15).date()
