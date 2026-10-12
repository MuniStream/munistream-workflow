"""El portal recibe el desenlace (p. ej. "rejected") y su motivo."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.endpoints.public_auth import desenlace_publico


def test_rechazado_con_motivo():
    inst = SimpleNamespace(terminal_status="rejected", terminal_message="Opinión técnica no favorable")
    assert desenlace_publico(inst) == {
        "terminal_status": "rejected", "terminal_message": "Opinión técnica no favorable"}


def test_instancia_sin_desenlace():
    assert desenlace_publico(SimpleNamespace(terminal_status=None, terminal_message=None)) == {
        "terminal_status": None, "terminal_message": None}


def test_instancia_vieja_sin_atributos():
    assert desenlace_publico(SimpleNamespace()) == {"terminal_status": None, "terminal_message": None}
