"""
Desenlace terminal de negocio (p. ej. "rejected"): una instancia que termina así
queda completada —no fallida— con su motivo, y lo pendiente se omite.
"""
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.workflows.dag import DAG
from app.workflows.operators.base import BaseOperator, TaskResult, TaskStatus
from app.workflows.operators.python import PythonOperator


def _dag():
    with DAG(dag_id="t_desenlace", name="t") as dag:
        a = PythonOperator(task_id="a", python_callable=lambda **c: {})
        b = PythonOperator(task_id="b", python_callable=lambda **c: {})
        c = PythonOperator(task_id="c", python_callable=lambda **c: {})
        a >> b >> c
    return dag


class _Termina(BaseOperator):
    def execute(self, context):
        return TaskResult(status=TaskStatus.TERMINATE,
                          data={"terminal_status": "rejected", "terminal_message": "m"})


def test_terminate_existe():
    assert TaskStatus.TERMINATE.value == "terminate"


def test_terminate_cierra_lo_pendiente():
    inst = _dag().create_instance(user_id="u", instance_data={})
    inst.update_task_status("a", "completed")
    saltadas = inst.terminate("rejected", "No procede por opinión técnica")
    assert saltadas == {"b", "c"}
    assert inst.is_completed()
    assert not inst.has_failed()
    assert inst.terminal_status == "rejected"
    assert inst.terminal_message == "No procede por opinión técnica"


def test_terminal_status_arranca_vacio():
    inst = _dag().create_instance(user_id="u", instance_data={})
    assert inst.terminal_status is None and inst.terminal_message is None


def test_run_conserva_la_salida_al_terminar():
    op = _Termina(task_id="x")
    assert op.run({}) == TaskStatus.TERMINATE
    assert op.state.output_data["terminal_status"] == "rejected"


class _DbInstance:
    """Lo mínimo de WorkflowInstance que toca `_save_instance_state`."""

    def __init__(self):
        self.current_step = None
        self.pre_task_context_snapshots = None
        self.status = "running"
        self.completed_at = None
        self.duration_seconds = None
        self.started_at = None
        self.terminal_status = None
        self.terminal_message = None
        self.guardado = False

    async def save(self):
        self.guardado = True


async def test_el_desenlace_se_persiste():
    from app.workflows.executor import DAGExecutor

    inst = _dag().create_instance(user_id="u", instance_data={})
    inst.update_task_status("a", "completed")
    inst.terminate("rejected", "Motivo")
    db = _DbInstance()
    await DAGExecutor()._save_instance_state(inst, db, "i-1", new_status="completed", record_executions=False)
    assert db.guardado
    assert db.terminal_status == "rejected" and db.terminal_message == "Motivo"


async def test_sin_desenlace_no_se_toca():
    from app.workflows.executor import DAGExecutor

    inst = _dag().create_instance(user_id="u", instance_data={})
    db = _DbInstance()
    db.terminal_status = "rejected"
    await DAGExecutor()._save_instance_state(inst, db, "i-1", record_executions=False)
    assert db.terminal_status == "rejected"


def test_el_desenlace_se_recarga_de_la_base():
    # Tras un rewind la base limpia el desenlace; la instancia en memoria no
    # puede volver a escribirlo en el siguiente guardado.
    from app.workflows.executor import sincronizar_desenlace

    inst = _dag().create_instance(user_id="u", instance_data={})
    inst.terminal_status, inst.terminal_message = "rejected", "viejo"
    db = _DbInstance()
    sincronizar_desenlace(inst, db)
    assert inst.terminal_status is None and inst.terminal_message is None

    db.terminal_status, db.terminal_message = "rejected", "Motivo"
    sincronizar_desenlace(inst, db)
    assert inst.terminal_status == "rejected" and inst.terminal_message == "Motivo"
