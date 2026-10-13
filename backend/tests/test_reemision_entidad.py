"""
Reemisión de una entidad (p. ej. la prórroga de un título): misma entidad,
vigencia nueva contada desde su vencimiento vigente, e historial de lo anterior.
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.workflows.operators.entity_operators import (
    EntityCreationOperator,
    EntityUpdateOperator,
    armar_reemision,
    resolver_vigencia,
)

AHORA = datetime(2026, 10, 12)
CFG = {"source": "resolucion_vigencia_anos", "desde": "valid_until_actual", "unidad_default": "anos"}


def test_cuenta_desde_el_vencimiento_vigente():
    vence = resolver_vigencia(CFG, {}, {"resolucion_vigencia_anos": 5}, AHORA,
                              vigente_hasta=datetime(2027, 3, 1))
    assert vence == datetime(2032, 3, 1)


def test_ya_vencido_cuenta_desde_su_vencimiento():
    # Decisión de negocio: la prórroga corre desde el vencimiento vigente,
    # aunque ya haya pasado.
    vence = resolver_vigencia(CFG, {}, {"resolucion_vigencia_anos": 5}, AHORA,
                              vigente_hasta=datetime(2025, 1, 1))
    assert vence == datetime(2030, 1, 1)


def test_sin_vencimiento_previo_cuenta_desde_hoy():
    vence = resolver_vigencia(CFG, {}, {"resolucion_vigencia_anos": "5"}, AHORA, vigente_hasta=None)
    assert vence == datetime(2031, 10, 12)


def test_sin_vigencia_resuelta_no_hay_fecha():
    assert resolver_vigencia(CFG, {}, {}, AHORA, vigente_hasta=datetime(2027, 3, 1)) is None


def test_creacion_sigue_resolviendo_su_vigencia():
    op = EntityCreationOperator(task_id="t", entity_type="x", vigencia={"anos": 2})
    vence = op._resolver_vigencia({}, {})
    assert vence is not None and vence.year == datetime.utcnow().year + 2


def test_reemision_guarda_historial_y_firma_anterior():
    upd = armar_reemision(
        actual_data={"signature": {"subject": "Firmante 1"}, "historial_prorrogas": [{"previo": 1}]},
        actual_valid_until=datetime(2027, 3, 1),
        updates_data={"signature": {"subject": "Firmante 2"}},
        nuevo_valid_until=datetime(2032, 3, 1),
        historial="historial_prorrogas", instance_id="inst-1", ahora=AHORA)
    assert upd["valid_until"] == datetime(2032, 3, 1)
    assert upd["data"]["signature"] == {"subject": "Firmante 2"}
    assert upd["data"]["historial_prorrogas"][0] == {"previo": 1}
    reg = upd["data"]["historial_prorrogas"][-1]
    assert reg["valid_until_anterior"] == "2027-03-01T00:00:00"
    assert reg["valid_until_nuevo"] == "2032-03-01T00:00:00"
    assert reg["signature_anterior"] == {"subject": "Firmante 1"}
    assert reg["instance_id"] == "inst-1"
    assert reg["fecha"] == AHORA.isoformat()


def test_sin_vigencia_no_toca_valid_until():
    upd = armar_reemision({}, None, {"x": 1}, None, None, None, AHORA)
    assert "valid_until" not in upd and upd["data"] == {"x": 1}


def test_update_operator_acepta_vigencia_e_historial():
    op = EntityUpdateOperator(task_id="r", entity_id_source="ids", vigencia=CFG,
                              historial="historial_prorrogas")
    assert op.vigencia == CFG and op.historial == "historial_prorrogas"


def test_fuente_anidada_como_la_deja_el_formulario():
    # El formulario de resolución deja su captura en `resolucion_data`.
    cfg = dict(CFG, source="resolucion_data.resolucion_vigencia_anos")
    vence = resolver_vigencia(cfg, {}, {"resolucion_data": {"resolucion_vigencia_anos": "5"}},
                              AHORA, vigente_hasta=datetime(2027, 3, 1))
    assert vence == datetime(2032, 3, 1)


def test_fuente_con_alternativas():
    cfg = dict(CFG, source="resolucion_data.resolucion_vigencia_anos|vigencia")
    vence = resolver_vigencia(cfg, {}, {"vigencia": "2 años"}, AHORA,
                              vigente_hasta=datetime(2027, 3, 1))
    assert vence == datetime(2029, 3, 1)


class _Entidad:
    def __init__(self, data, valid_until):
        self.data, self.valid_until, self.entity_id, self.entity_type = data, valid_until, "t-1", "x"


async def test_la_reemision_no_toma_la_vigencia_vieja_del_titulo(monkeypatch):
    # El título guarda la vigencia de SU resolución original (10 años); la
    # prórroga tiene que usar la de la resolución nueva (5).
    from app.services.entity_service import EntityService
    from app.workflows.operators.entity_operators import EntityUpdateOperator

    actual = _Entidad({"resolucion_data": {"resolucion_vigencia_anos": 10},
                       "resolucion_vigencia_anos": 10}, datetime(2025, 1, 1))
    capturado = {}

    async def get_entity(entity_id, owner=None):
        return actual

    async def update_entity(entity_id, updates, owner=None):
        capturado.update(updates)
        return actual

    monkeypatch.setattr(EntityService, "get_entity", staticmethod(get_entity))
    monkeypatch.setattr(EntityService, "update_entity", staticmethod(update_entity))

    op = EntityUpdateOperator(task_id="r", entity_id_source="ids",
                              vigencia=dict(CFG, source="resolucion_data.resolucion_vigencia_anos"))
    op.log_info = _sin_log
    res = await op.execute_async({"ids": ["t-1"], "resolucion_data": {"resolucion_vigencia_anos": 5}})
    assert res.status.value == "continue"
    assert capturado["valid_until"] == datetime(2030, 1, 1)


async def test_vigencia_requerida_que_no_resuelve_falla(monkeypatch):
    # Prorrogar sin vigencia nueva no puede pasar en silencio: el título
    # quedaría "prorrogado", firmado y con historial, pero sin extender.
    from app.services.entity_service import EntityService
    from app.workflows.operators.entity_operators import EntityUpdateOperator

    async def get_entity(entity_id, owner=None):
        return _Entidad({}, datetime(2027, 3, 1))

    async def update_entity(entity_id, updates, owner=None):
        raise AssertionError("no debe escribir")

    monkeypatch.setattr(EntityService, "get_entity", staticmethod(get_entity))
    monkeypatch.setattr(EntityService, "update_entity", staticmethod(update_entity))
    op = EntityUpdateOperator(task_id="r", entity_id_source="ids", vigencia=CFG,
                              vigencia_requerida=True)
    op.log_info = _sin_log
    op.log_error = _sin_log
    res = await op.execute_async({"ids": ["t-1"]})
    assert res.status.value == "failed"
    assert "vigencia" in (res.error or "")


async def _sin_log(*a, **k):
    return None
