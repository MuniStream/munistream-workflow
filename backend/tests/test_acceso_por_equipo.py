"""
La asignación por equipo no concedía acceso a nadie.

`check_instance_access` tiene una rama para ello:

    if "manager" in user_roles:
        if instance.assigned_team_id and instance.assigned_team_id in user_teams:
            return True

pero `user_teams` sale de `current_user.get("teams", [])`, y **`get_user_info`
nunca pone esa clave**: devuelve `sub`, `email`, `username`, `name` y `roles`, y
nada más. Así que la lista es siempre vacía y la rama no puede conceder acceso
jamás.

El resultado es que los trámites asignados a un equipo solo los ve quien entra
como `admin` —que ve todo—, y la separación por áreas es decorativa. Se nota al
crear el hijo de opinión técnica: queda `assigned_to: imipas, pending_review` y no
hay bandeja que lo reciba.

Los equipos sí existen como dato (`TeamModel`, con sus miembros y sus endpoints de
administración); lo que faltaba era cruzarlos con quien pregunta.

**Por qué se resuelven desde la base y no desde el token.** Meterlos como claim
exigiría mapear grupos en Keycloak y mantener esa correspondencia en los tres
entornos, y el equipo ya vive en Mongo con su lista de miembros. Resolver ahí es
una consulta por petición contra el dato que ya es la fuente de verdad.

La decisión se separa de la consulta para poder probarla sin Mongo: aquí se
inyectan los equipos ya resueltos.
"""

import os
import sys
from types import SimpleNamespace

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.api.endpoints.instances import puede_ver_instancia


def _instancia(team=None, usuario=None):
    return SimpleNamespace(assigned_team_id=team, assigned_user_id=usuario)


# ---------------------------------------------------------------------------
# Lo que ya valía
# ---------------------------------------------------------------------------

def test_el_admin_lo_ve_todo():
    assert puede_ver_instancia(_instancia(team="imipas"), ["admin"], "u1", []) is True


def test_una_asignacion_directa_basta():
    assert puede_ver_instancia(_instancia(usuario="u1"), ["reviewer"], "u1", []) is True


def test_un_revisor_ajeno_no_entra():
    assert puede_ver_instancia(_instancia(usuario="otro"), ["reviewer"], "u1", []) is False


# ---------------------------------------------------------------------------
# Lo que no valía: la asignación por equipo
# ---------------------------------------------------------------------------

def test_el_miembro_del_equipo_asignado_entra():
    """Es el caso que nunca podía darse: `user_teams` llegaba siempre vacía."""
    assert puede_ver_instancia(_instancia(team="imipas"), ["manager"], "u1", ["imipas"]) is True


def test_quien_no_es_del_equipo_no_entra():
    assert puede_ver_instancia(_instancia(team="imipas"), ["manager"], "u1", ["fomento_pesquero"]) is False


def test_un_revisor_del_equipo_tambien_entra():
    """
    Restringirlo a `manager` dejaba fuera a quien de verdad dictamina: un revisor
    del área. Un equipo al que solo pueden entrar los jefes no es una bandeja.
    """
    assert puede_ver_instancia(_instancia(team="imipas"), ["reviewer"], "u1", ["imipas"]) is True


def test_sin_equipo_asignado_la_pertenencia_no_abre_nada():
    """Pertenecer a un equipo no da acceso a lo que no está asignado a ninguno."""
    assert puede_ver_instancia(_instancia(), ["manager"], "u1", ["imipas"]) is False


def test_un_viewer_del_equipo_no_decide_pero_ve():
    assert puede_ver_instancia(_instancia(team="imipas"), ["viewer"], "u1", ["imipas"]) is True


def test_sin_rol_ninguno_no_entra():
    """Pertenecer al equipo sin ningún rol del sistema no alcanza."""
    assert puede_ver_instancia(_instancia(team="imipas"), [], "u1", ["imipas"]) is False
