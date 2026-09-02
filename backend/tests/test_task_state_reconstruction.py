"""
Regression tests for task_state reconstruction.

Contexto: una instancia que el ejecutor rehidrata desde la base de datos
(`DAGExecutor.execute_instance` cuando la instancia ya no vive en el DAGBag,
p. ej. tras reiniciar el backend) reconstruía `task_states` con diccionarios
parciales `{"status": ...}`. Al arrancar el primer paso,
`DAGInstance.update_task_status(task_id, "executing")` lee `started_at` para
conservar la marca de tiempo original y reventaba con `KeyError: 'started_at'`,
abortando el ciclo del ejecutor sin persistir nada: la instancia quedaba en
`running` con cero pasos y ningún reintento lograba avanzarla.

Estas pruebas son puras (sin Mongo ni Keycloak).
"""

import os
import sys

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.workflows.dag import DAG, new_task_state
from app.workflows.operators.base import BaseOperator, TaskStatus


class _NoopOperator(BaseOperator):
    """Operador mínimo: no hace nada y continúa."""

    def execute(self, context):
        return TaskStatus.CONTINUE


def _build_instance():
    dag = DAG(dag_id="dag_prueba", name="DAG de prueba")
    dag.add_task(_NoopOperator(task_id="primer_paso"))
    dag.build_graph()
    return dag, dag.create_instance(user_id="usuario-prueba")


TASK_STATE_KEYS = {"status", "started_at", "completed_at", "result", "error"}


def test_new_task_state_tiene_la_forma_canonica():
    """El helper es la única fuente de la forma de un task_state."""
    assert set(new_task_state().keys()) == TASK_STATE_KEYS
    assert new_task_state()["status"] == "pending"
    assert new_task_state("completed")["status"] == "completed"
    assert new_task_state()["started_at"] is None


def test_update_task_status_executing_sobre_estado_reconstruido():
    """
    Reproduce el fallo: un task_state rehidratado sin `started_at` no debe
    tumbar el arranque del paso.
    """
    _, instance = _build_instance()

    # Así rehidrataba el ejecutor una instancia venida de la base de datos.
    instance.task_states["primer_paso"] = {"status": "pending"}

    instance.update_task_status("primer_paso", "executing")

    assert instance.task_states["primer_paso"]["status"] == "executing"
    assert instance.task_states["primer_paso"]["started_at"] is not None
    assert instance.current_task == "primer_paso"


def test_update_task_status_conserva_el_started_at_original():
    """El arreglo no debe romper la razón por la que se leía `started_at`."""
    _, instance = _build_instance()

    instance.update_task_status("primer_paso", "executing")
    primer_arranque = instance.task_states["primer_paso"]["started_at"]

    instance.update_task_status("primer_paso", "waiting")
    instance.update_task_status("primer_paso", "executing")

    assert instance.task_states["primer_paso"]["started_at"] == primer_arranque
