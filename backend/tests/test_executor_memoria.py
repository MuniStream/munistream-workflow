"""El ejecutor no retiene en memoria lo que no está corriendo.

Con cientos de trámites en espera (validación humana), cada sondeo dejaba la
instancia —con su contexto completo— en la caché del DAGBag para siempre, y el
arranque cargaba todas las instancias completas solo para leer su id. El
backend crecía hasta que el sistema lo mataba por memoria.
"""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.workflows.dag import InstanceStatus
from tests.test_executor_queue_recovery import _make_executor


def _con_instancia(executor, iid, status):
    executor.workflow_service.dag_bag.instances[iid] = SimpleNamespace(status=status, task_states={})


async def _ejecuta(executor, puede_seguir=True):
    async def _ok(_iid):
        return puede_seguir
    executor.execute_instance = _ok


@pytest.mark.asyncio
async def test_una_instancia_en_pausa_sale_de_la_cache_tras_programarse():
    ex = _make_executor()
    _con_instancia(ex, "a", InstanceStatus.PAUSED)
    await _ejecuta(ex)
    await ex._execute_queued_instance("a")
    assert "a" in ex.waiting_queue                       # quedó programada
    assert "a" not in ex.workflow_service.dag_bag.instances  # y liberada


@pytest.mark.asyncio
async def test_una_instancia_corriendo_se_queda_en_cache():
    ex = _make_executor()
    _con_instancia(ex, "a", InstanceStatus.RUNNING)
    await _ejecuta(ex)
    await ex._execute_queued_instance("a")
    assert "a" in ex.workflow_service.dag_bag.instances


@pytest.mark.asyncio
async def test_una_instancia_terminada_sale_de_la_cache():
    ex = _make_executor()
    _con_instancia(ex, "a", InstanceStatus.COMPLETED)
    await _ejecuta(ex, puede_seguir=False)
    await ex._execute_queued_instance("a")
    assert "a" not in ex.workflow_service.dag_bag.instances


@pytest.mark.asyncio
async def test_los_tiempos_de_ejecucion_no_crecen_sin_tope():
    ex = _make_executor()
    _con_instancia(ex, "a", InstanceStatus.RUNNING)
    await _ejecuta(ex)
    for _ in range(200):
        await ex._execute_queued_instance("a")
    assert len(ex._task_execution_times["a"]) <= 20


@pytest.mark.asyncio
async def test_el_arranque_solo_lee_id_y_estado(monkeypatch):
    from app.models.workflow import WorkflowInstance

    pedidas = {}

    class _Cursor:
        def __init__(self, docs):
            self.docs = docs

        def __aiter__(self):
            self._it = iter(self.docs)
            return self

        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

    class _Coleccion:
        def find(self, filtro, proyeccion=None):
            pedidas["filtro"], pedidas["proyeccion"] = filtro, proyeccion
            return _Cursor([{"instance_id": "x", "status": "paused"},
                            {"instance_id": "y", "status": "running"}])

    monkeypatch.setattr(WorkflowInstance, "get_motor_collection", classmethod(lambda cls: _Coleccion()))
    ex = _make_executor()
    n = await ex.load_incomplete_instances()
    assert n == 2
    assert set(pedidas["proyeccion"]) <= {"instance_id", "status", "_id"}
    assert set(ex.waiting_queue) == {"x", "y"}
