"""
Unit tests for the event-driven scheduling logic in DAGExecutor.

These tests exercise the pure scheduling decisions (no Mongo / Keycloak):
- _get_instance_polling_decision: classify waiting tasks by strategy
- _schedule_next_wakeup: pick the right queue and delay
- resume_instance: contract for event-driven wake-up

The executor's _execution_loop is not started; each test calls the
relevant method directly on a constructed DAGExecutor instance.
"""

import sys
import time
import types
from types import SimpleNamespace

import pytest

# Ensure the backend `app` package is importable when running pytest from
# the repo root without installing the package.
import os
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.workflows.dag import InstanceStatus
from app.workflows.executor import DAGExecutor
from app.workflows.polling_strategy import (
    OperatorPollingStrategy,
    PollingConfig,
)


class _StubOperator:
    """Minimal operator stub that exposes get_polling_config()."""

    def __init__(self, polling_config: PollingConfig):
        self._cfg = polling_config

    def get_polling_config(self) -> PollingConfig:
        return self._cfg


def _make_dag_instance(task_states, tasks):
    dag = SimpleNamespace(tasks=tasks)
    return SimpleNamespace(
        task_states=task_states,
        dag=dag,
        status=InstanceStatus.PAUSED,
        context={},
    )


def _make_executor():
    instances = {}
    dag_bag = SimpleNamespace(
        instances=instances,
        get_instance=lambda iid, _store=instances: _store.get(iid),
    )
    workflow_service = SimpleNamespace(dag_bag=dag_bag)
    executor = object.__new__(DAGExecutor)
    executor.workflow_service = workflow_service
    executor.execution_queue = []
    executor.active_queue = []
    executor.waiting_queue = {}
    executor.throttled_queue = {}
    executor._instance_next_execution_time = {}
    executor._task_execution_times = {}
    executor._last_execution_time = {}
    # asyncio.Event would require a running loop in some test setups; replace
    # with a no-op stand-in since we never await on it here.
    executor._work_available = SimpleNamespace(set=lambda: None, clear=lambda: None)
    return executor


def _register_instance(executor, instance_id, dag_instance):
    executor.workflow_service.dag_bag.instances[instance_id] = dag_instance


# --------------------------------------------------------------------------
# _get_instance_polling_decision
# --------------------------------------------------------------------------

def test_polling_decision_returns_none_when_all_event_driven():
    executor = _make_executor()
    tasks = {
        "approval": _StubOperator(PollingConfig.event_driven()),
        "form": _StubOperator(PollingConfig.event_driven()),
    }
    task_states = {
        "approval": {"status": "waiting"},
        "form": {"status": "waiting_input"},
    }
    dag_instance = _make_dag_instance(task_states, tasks)

    assert executor._get_instance_polling_decision(dag_instance) is None


def test_polling_decision_picks_min_interval():
    executor = _make_executor()
    tasks = {
        "airflow_a": _StubOperator(PollingConfig.polling(30)),
        "airflow_b": _StubOperator(PollingConfig.polling(10)),
    }
    task_states = {
        "airflow_a": {"status": "waiting"},
        "airflow_b": {"status": "waiting"},
    }
    dag_instance = _make_dag_instance(task_states, tasks)

    assert executor._get_instance_polling_decision(dag_instance) == 10.0


def test_polling_decision_ignores_event_driven_when_polling_present():
    executor = _make_executor()
    tasks = {
        "approval": _StubOperator(PollingConfig.event_driven()),
        "airflow": _StubOperator(PollingConfig.polling(20)),
    }
    task_states = {
        "approval": {"status": "waiting_approval"},
        "airflow": {"status": "waiting"},
    }
    dag_instance = _make_dag_instance(task_states, tasks)

    assert executor._get_instance_polling_decision(dag_instance) == 20.0


def test_polling_decision_skips_non_waiting_tasks():
    executor = _make_executor()
    tasks = {
        "done": _StubOperator(PollingConfig.polling(5)),
        "approval": _StubOperator(PollingConfig.event_driven()),
    }
    task_states = {
        "done": {"status": "completed"},  # finished -> ignore even if POLLING
        "approval": {"status": "waiting"},
    }
    dag_instance = _make_dag_instance(task_states, tasks)

    assert executor._get_instance_polling_decision(dag_instance) is None


# --------------------------------------------------------------------------
# _schedule_next_wakeup
# --------------------------------------------------------------------------

def test_schedule_wakeup_event_driven_paused_uses_safety_net():
    from app.core.config import settings
    settings.EXECUTOR_SAFETY_NET_SECONDS = 300

    executor = _make_executor()
    tasks = {"approval": _StubOperator(PollingConfig.event_driven())}
    task_states = {"approval": {"status": "waiting"}}
    dag_instance = _make_dag_instance(task_states, tasks)
    _register_instance(executor, "inst-1", dag_instance)

    before = time.time()
    executor._schedule_next_wakeup("inst-1")

    assert "inst-1" not in executor.throttled_queue
    assert "inst-1" in executor.waiting_queue
    delay = executor.waiting_queue["inst-1"] - before
    assert 299 <= delay <= 301


def test_schedule_wakeup_event_driven_with_safety_net_disabled():
    from app.core.config import settings
    original = settings.EXECUTOR_SAFETY_NET_SECONDS
    settings.EXECUTOR_SAFETY_NET_SECONDS = 0
    try:
        executor = _make_executor()
        tasks = {"approval": _StubOperator(PollingConfig.event_driven())}
        task_states = {"approval": {"status": "waiting"}}
        dag_instance = _make_dag_instance(task_states, tasks)
        _register_instance(executor, "inst-1", dag_instance)

        executor._schedule_next_wakeup("inst-1")

        assert "inst-1" not in executor.throttled_queue
        assert "inst-1" not in executor.waiting_queue
    finally:
        settings.EXECUTOR_SAFETY_NET_SECONDS = original


def test_schedule_wakeup_polling_paused_uses_min_interval():
    executor = _make_executor()
    tasks = {
        "approval": _StubOperator(PollingConfig.event_driven()),
        "airflow": _StubOperator(PollingConfig.polling(15)),
    }
    task_states = {
        "approval": {"status": "waiting"},
        "airflow": {"status": "waiting"},
    }
    dag_instance = _make_dag_instance(task_states, tasks)
    _register_instance(executor, "inst-2", dag_instance)

    before = time.time()
    executor._schedule_next_wakeup("inst-2")

    assert "inst-2" not in executor.throttled_queue
    delay = executor.waiting_queue["inst-2"] - before
    assert 14 <= delay <= 16


def test_entity_picker_discovery_cache_round_trip():
    from app.workflows.operators.entity_picker_operator import EntityPickerOperator

    op = object.__new__(EntityPickerOperator)
    op.task_id = "pick_doc"

    entity = SimpleNamespace(
        entity_id="e1", entity_type="document", name="Doc", data={"k": "v"}
    )
    discovery = {"docs": [entity]}

    context = {}
    op._store_discovery_cache(context, discovery)
    assert op._discovery_cache_key in context

    rehydrated = op._load_cached_discovery(context)
    assert rehydrated["docs"][0].entity_id == "e1"
    assert rehydrated["docs"][0].data == {"k": "v"}

    op._invalidate_discovery_cache(context)
    assert op._discovery_cache_key not in context
    assert op._load_cached_discovery({}) is None


def test_schedule_wakeup_running_uses_short_throttle():
    from app.core.config import settings
    settings.EXECUTOR_RUNNING_THROTTLE_SECONDS = 0.5

    executor = _make_executor()
    tasks = {"t": _StubOperator(PollingConfig.event_driven())}
    task_states = {"t": {"status": "pending"}}
    dag_instance = _make_dag_instance(task_states, tasks)
    dag_instance.status = InstanceStatus.RUNNING
    _register_instance(executor, "inst-3", dag_instance)

    before = time.time()
    executor._schedule_next_wakeup("inst-3")

    assert "inst-3" in executor.throttled_queue
    assert "inst-3" not in executor.waiting_queue
    delay = executor.throttled_queue["inst-3"] - before
    assert 0.4 <= delay <= 0.7
