"""
La importación de usuarios desde Keycloak fallaba con todos.

`UserModel` exige `hashed_password` y la importación no lo ponía, así que los
cinco usuarios del realm local fallaban la validación y la colección `users`
quedaba VACÍA. Eso no se notaba porque los revisores entran como `admin`, que
cortocircuita toda comprobación de acceso — pero sin usuarios internos no hay a
quién meter en un equipo, y la asignación por área es decorativa.

Un usuario que viene de Keycloak **nunca se autentica con contraseña local**: lo
hace contra el IdP. Así que su `hashed_password` tiene que ser un valor que no
pueda casar con ninguna contraseña, no una vacía ni una fabricada que alguien
pudiera adivinar.

Son pruebas puras: no tocan Mongo ni Keycloak.
"""

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.services.keycloak_sync import SIN_CONTRASENA_LOCAL, datos_de_usuario_importado

KC = {
    "email": "revisor@conapesca.gob.mx",
    "username": "revisor",
    "firstName": "Ana",
    "lastName": "Ramírez",
    "enabled": True,
    "attributes": {},
}


def test_trae_lo_que_identifica_a_la_persona():
    d = datos_de_usuario_importado(KC)

    assert d["email"] == "revisor@conapesca.gob.mx"
    assert d["username"] == "revisor"
    assert d["full_name"] == "Ana Ramírez"


def test_pone_un_hash_que_no_puede_casar():
    """Sin esto, `UserModel` no valida y la importación falla con todos."""
    d = datos_de_usuario_importado(KC)

    assert d["hashed_password"] == SIN_CONTRASENA_LOCAL
    assert d["hashed_password"]


def test_el_hash_no_es_un_hash_valido_de_bcrypt():
    """
    Tiene que ser imposible de satisfacer: si fuera un hash real de alguna
    contraseña, existiría una contraseña que abre la cuenta sin pasar por el IdP.
    """
    assert not SIN_CONTRASENA_LOCAL.startswith("$2")
    assert len(SIN_CONTRASENA_LOCAL) > 0


def test_sin_nombre_ni_apellido_usa_algo_legible():
    """
    `full_name` es obligatorio. Con el nombre vacío la fila queda sin nada que
    mostrar en la interfaz, así que cae al usuario o al correo.
    """
    d = datos_de_usuario_importado({"email": "x@y.mx", "username": "equis", "enabled": True})

    assert d["full_name"] == "equis"


def test_sin_usuario_ni_nombre_cae_al_correo():
    d = datos_de_usuario_importado({"email": "x@y.mx", "enabled": True})

    assert d["full_name"] == "x@y.mx"
    assert d["username"] == "x@y.mx"


def test_un_usuario_deshabilitado_llega_inactivo():
    d = datos_de_usuario_importado({**KC, "enabled": False})

    assert d["status"] == "inactive"


def test_los_atributos_del_realm_se_respetan():
    d = datos_de_usuario_importado({**KC, "attributes": {"department": ["Dictamen"], "phone": ["555"]}})

    assert d["department"] == "Dictamen"
    assert d["phone"] == "555"


def test_un_rol_desconocido_no_tumba_la_importacion():
    """Un valor inesperado en el realm no puede impedir que el usuario exista."""
    d = datos_de_usuario_importado({**KC, "attributes": {"role": ["inventado"]}})

    assert d["role"] is not None
