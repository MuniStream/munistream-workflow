"""
Pruebas del encolado y la recuperación ante fallos del DAGExecutor.

Cubren dos defectos del ciclo de ejecución:

1. Inanición de la cola del arranque. `load_incomplete_instances()` metía las
   instancias en `execution_queue`, que el ciclo solo atiende cuando
   `active_queue` y `waiting_queue` están vacías. Como cada instancia pausada
   queda estacionada en `waiting_queue` con su safety net, esa condición no se
   cumple nunca y las instancias del arranque no se ejecutaban jamás.

2. Ausencia de aislamiento por instancia. Una excepción dentro de
   `execute_instance` subía hasta el `while` del ciclo; para entonces el id ya
   había salido de las colas, así que la instancia quedaba huérfana, sin cola y
   sin temporizador, hasta el siguiente reinicio del backend.

Al arreglarlos hay que cuidar el consumo de recursos: ni estampida de
ejecuciones al arrancar, ni reintentos en caliente sobre un fallo determinista.
Eso es lo que fijan las pruebas de escalonamiento y de backoff.

Son pruebas puras: no tocan Mongo ni Keycloak.
"""

import os
import sys

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from types import SimpleNamespace

from app.core.config import settings
from app.workflows.executor import DAGExecutor


def _make_executor():
    """Executor sin __init__: solo el estado de colas que ejercitamos aquí."""
    instances = {}
    dag_bag = SimpleNamespace(
        instances=instances,
        get_instance=lambda iid, _store=instances: _store.get(iid),
    )
    executor = object.__new__(DAGExecutor)
    executor.workflow_service = SimpleNamespace(dag_bag=dag_bag)
    executor.active_queue = []
    executor.waiting_queue = {}
    executor.throttled_queue = {}
    executor._instance_next_execution_time = {}
    executor._instance_failure_counts = {}
    executor._task_execution_times = {}
    executor._last_execution_time = {}
    # asyncio.Event necesitaría un loop corriendo; aquí solo nos interesa si se
    # señaló trabajo disponible.
    executor._work_signalled = False

    def _set():
        executor._work_signalled = True

    executor._work_available = SimpleNamespace(set=_set, clear=lambda: None)
    return executor


# --------------------------------------------------------------------------
# Carga del arranque: se estaciona donde el ciclo sí mira, y escalonada
# --------------------------------------------------------------------------

def test_carga_inicial_estaciona_las_instancias_en_waiting_queue():
    """
    Las instancias incompletas del arranque deben quedar en `waiting_queue`,
    que es la rama que el ciclo atiende de verdad.
    """
    executor = _make_executor()
    ids = ["inst-a", "inst-b", "inst-c"]

    executor._schedule_boot_instances(ids)

    assert set(executor.waiting_queue) == set(ids)


def test_carga_inicial_no_ejecuta_de_golpe():
    """Nada debe quedar en la cola activa: un reinicio no es una estampida."""
    executor = _make_executor()

    executor._schedule_boot_instances([f"inst-{n}" for n in range(50)])

    assert executor.active_queue == []


def test_carga_inicial_escalona_los_despertares():
    """
    Los despertares se reparten en el tiempo. Si todos vencieran a la vez, el
    ciclo movería las 127 instancias a la cola activa en una sola pasada y las
    ejecutaría seguidas, con su I/O a Mongo.
    """
    executor = _make_executor()
    ids = [f"inst-{n}" for n in range(20)]

    executor._schedule_boot_instances(ids)

    vencimientos = sorted(executor.waiting_queue.values())
    separaciones = [b - a for a, b in zip(vencimientos, vencimientos[1:])]
    assert all(s > 0 for s in separaciones), "los despertares deben ser distintos"


def test_carga_inicial_reparte_dentro_de_una_ventana_acotada():
    """El escalonamiento no puede diferir instancias indefinidamente."""
    import time

    executor = _make_executor()
    ids = [f"inst-{n}" for n in range(200)]

    inicio = time.time()
    executor._schedule_boot_instances(ids)

    ultimo = max(executor.waiting_queue.values())
    assert ultimo - inicio <= settings.EXECUTOR_BOOT_DRAIN_WINDOW_SECONDS + 1


def test_carga_inicial_con_pocas_instancias_no_espera_la_ventana_completa():
    """
    Repartir en la ventana completa siempre castigaría el caso barato: con dos
    instancias pendientes no tiene sentido esperar un minuto para arrancarlas.
    """
    import time

    executor = _make_executor()
    inicio = time.time()

    executor._schedule_boot_instances(["inst-a", "inst-b"])

    assert max(executor.waiting_queue.values()) - inicio <= 5


def test_submit_instance_encola_aunque_venga_del_arranque():
    """
    Una instancia ya conocida por la carga del arranque debe poder llegar a la
    cola activa cuando alguien la envía explícitamente. Antes el dedup contra
    la cola legacy convertía este `submit` en un no-op silencioso.
    """
    executor = _make_executor()
    executor._schedule_boot_instances(["inst-a"])

    executor.submit_instance("inst-a")

    assert "inst-a" in executor.active_queue
    assert executor._work_signalled is True


# --------------------------------------------------------------------------
# Backoff de fallos: acotado y con final
# --------------------------------------------------------------------------

def test_backoff_de_fallos_nunca_es_inmediato():
    """Un backoff de cero reintentaría en caliente y quemaría CPU."""
    executor = _make_executor()

    for intento in range(1, settings.EXECUTOR_MAX_CONSECUTIVE_FAILURES):
        assert executor._failure_backoff(intento) > 0


def test_backoff_de_fallos_crece_y_tiene_tope():
    executor = _make_executor()

    esperas = [
        executor._failure_backoff(n)
        for n in range(1, settings.EXECUTOR_MAX_CONSECUTIVE_FAILURES)
    ]

    assert esperas == sorted(esperas), "el backoff debe ser no decreciente"
    assert max(esperas) <= settings.EXECUTOR_MAX_FAILURE_BACKOFF_SECONDS


def test_backoff_de_fallos_termina_tras_el_maximo():
    """Agotados los intentos no se reprograma más: un fallo determinista para."""
    executor = _make_executor()

    assert executor._failure_backoff(settings.EXECUTOR_MAX_CONSECUTIVE_FAILURES) is None


# --------------------------------------------------------------------------
# Aislamiento del fallo por instancia
# --------------------------------------------------------------------------

def _executor_que_falla(executor, excepcion=RuntimeError("boom")):
    async def _raise(_instance_id):
        raise excepcion

    executor.execute_instance = _raise
    return executor


@pytest.mark.asyncio
async def test_instancia_que_falla_se_reprograma_en_vez_de_quedar_huerfana():
    import time

    executor = _executor_que_falla(_make_executor())
    ahora = time.time()

    await executor._execute_queued_instance("inst-a")

    assert "inst-a" in executor.waiting_queue
    assert executor.waiting_queue["inst-a"] > ahora
    assert executor._instance_failure_counts["inst-a"] == 1


@pytest.mark.asyncio
async def test_una_instancia_que_falla_no_tumba_a_las_demas():
    """La excepción se contiene: el ciclo sigue sirviendo al resto."""
    executor = _executor_que_falla(_make_executor())

    await executor._execute_queued_instance("inst-a")

    executor.active_queue.append("inst-b")
    assert executor.active_queue == ["inst-b"]


@pytest.mark.asyncio
async def test_instancia_que_falla_siempre_deja_de_reintentarse():
    executor = _executor_que_falla(_make_executor())

    for _ in range(settings.EXECUTOR_MAX_CONSECUTIVE_FAILURES):
        await executor._execute_queued_instance("inst-a")

    assert "inst-a" not in executor.waiting_queue
    assert "inst-a" not in executor.active_queue
    assert "inst-a" not in executor.throttled_queue


@pytest.mark.asyncio
async def test_el_contador_de_fallos_no_crece_sin_limite():
    """Agotados los intentos, la entrada se descarta: el dict no es una fuga."""
    executor = _executor_que_falla(_make_executor())

    for _ in range(settings.EXECUTOR_MAX_CONSECUTIVE_FAILURES):
        await executor._execute_queued_instance("inst-a")

    assert "inst-a" not in executor._instance_failure_counts


@pytest.mark.asyncio
async def test_una_ejecucion_exitosa_reinicia_el_contador_de_fallos():
    executor = _executor_que_falla(_make_executor())
    await executor._execute_queued_instance("inst-a")
    assert executor._instance_failure_counts["inst-a"] == 1

    async def _ok(_instance_id):
        return False

    executor.execute_instance = _ok

    await executor._execute_queued_instance("inst-a")

    assert "inst-a" not in executor._instance_failure_counts
