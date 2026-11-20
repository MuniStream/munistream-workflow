"""Public indicators never publish counts that single out fewer than five cases."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.endpoints.atencion import MIN_PUBLICABLE, _desglose_publicable, _publicable


def test_minimo_publicable_es_cinco():
    assert MIN_PUBLICABLE == 5


def test_conteos_pequenos_se_suprimen():
    assert _publicable(0) is None
    assert _publicable(4) is None
    assert _publicable(5) == 5


def test_desglose_con_ceros_se_publica():
    assert _desglose_publicable({"_aprobados": 8, "_rechazados": 0}, 8) is True


def test_desglose_que_revelaria_un_caso_por_resta_se_suprime():
    # 8 resueltos, 7 aprobados: publicar 7 revela 1 rechazo.
    assert _desglose_publicable({"_aprobados": 7, "_rechazados": 1}, 8) is False


def test_desglose_con_pocos_resueltos_se_suprime():
    assert _desglose_publicable({"_aprobados": 4, "_rechazados": 0}, 4) is False


def test_desglose_con_ambos_suficientes_se_publica():
    assert _desglose_publicable({"_aprobados": 6, "_rechazados": 5}, 11) is True
