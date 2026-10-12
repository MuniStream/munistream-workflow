"""Estado efectivo de una instancia: un trámite RECHAZADO termina en el motor
como `completed` con `terminal_status="rejected"`; para el personal y para las
métricas es un rechazo, no un trámite completado."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.estado_instancia import estado_efectivo, filtro_estado, contar_por_estado


def test_completado_con_rechazo_es_rechazado():
    assert estado_efectivo("completed", "rejected") == "rejected"


def test_completado_normal_sigue_completado():
    assert estado_efectivo("completed", None) == "completed"


def test_otros_estados_no_cambian():
    assert estado_efectivo("failed", None) == "failed"
    assert estado_efectivo("running", None) == "running"


def test_acepta_la_instancia_entera():
    inst = SimpleNamespace(status="completed", terminal_status="rejected")
    assert estado_efectivo(inst) == "rejected"


def test_filtro_rechazado():
    assert filtro_estado("rejected") == {"status": "completed", "terminal_status": "rejected"}


def test_filtro_completado_excluye_rechazados():
    assert filtro_estado("completed") == {"status": "completed", "terminal_status": {"$ne": "rejected"}}


def test_filtro_otro_estado():
    assert filtro_estado("failed") == {"status": "failed"}


def test_contar_por_estado():
    instancias = [
        SimpleNamespace(status="completed", terminal_status=None),
        SimpleNamespace(status="completed", terminal_status="rejected"),
        SimpleNamespace(status="failed", terminal_status=None),
        SimpleNamespace(status="completed", terminal_status=None),
    ]
    assert contar_por_estado(instancias) == {"completed": 2, "rejected": 1, "failed": 1}


def _inst(**k):
    base = dict(instance_id="i-1", workflow_id="w", user_id="u", status="completed",
                terminal_status=None, terminal_message=None, current_step=None, context={},
                created_at=None, updated_at=None, completed_at=None, assigned_user_id=None,
                assigned_team_id=None, assignment_status=None, assignment_type=None,
                assigned_at=None, assigned_by=None, assignment_notes=None)
    base.update(k)
    return SimpleNamespace(**base)


def test_la_respuesta_del_admin_muestra_rechazado():
    from datetime import datetime
    from app.api.endpoints.instances import convert_instance_to_response
    r = convert_instance_to_response(_inst(terminal_status="rejected", terminal_message="Motivo",
                                           created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 1)))
    assert r.status == "rejected"
    assert r.terminal_status == "rejected" and r.terminal_message == "Motivo"


def test_la_busqueda_filtra_rechazados():
    from app.services.instance_search import construir_consulta
    assert construir_consulta(status="rejected") == {"status": "completed", "terminal_status": "rejected"}
    assert construir_consulta(status="completed")["terminal_status"] == {"$ne": "rejected"}
