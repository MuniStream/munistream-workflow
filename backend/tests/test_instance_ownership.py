"""
Autorización a nivel de objeto para los trámites de un ciudadano.

`GET /api/v1/public/track/{instance_id}` exigía autenticación pero no
comprobaba que el trámite fuera del ciudadano que lo pedía: cualquier ciudadano
autenticado podía leer el estado del trámite de otro con solo tener el UUID.
El docstring del endpoint delataba el razonamiento — daba por buena la
autenticación como si fuera autorización.

La comprobación existía copiada a mano en `rewind` y en `submit-data`, y estaba
ausente justo en el endpoint de lectura. Estas pruebas fijan el invariante en un
único sitio con nombre propio, para que el siguiente endpoint no vuelva a
olvidarlo.

Son pruebas puras: no tocan Mongo ni Keycloak.
"""

import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.api.endpoints.public_auth import require_instance_owner


def _instancia(user_id):
    return SimpleNamespace(instance_id="inst-a", user_id=user_id)


def _ciudadano(customer_id):
    return SimpleNamespace(id=customer_id)


def test_el_dueno_del_tramite_pasa():
    require_instance_owner(_instancia("cliente-1"), _ciudadano("cliente-1"))


def test_otro_ciudadano_recibe_403():
    with pytest.raises(HTTPException) as exc:
        require_instance_owner(_instancia("cliente-1"), _ciudadano("cliente-2"))

    assert exc.value.status_code == 403


def test_el_403_no_revela_de_quien_es_el_tramite():
    """El mensaje de error no debe filtrar el id del dueño real."""
    with pytest.raises(HTTPException) as exc:
        require_instance_owner(_instancia("cliente-1"), _ciudadano("cliente-2"))

    assert "cliente-1" not in str(exc.value.detail)


def test_un_tramite_sin_dueno_no_es_de_nadie():
    """
    Falla cerrado: una instancia sin `user_id` no puede quedar legible para
    cualquiera que la pida.
    """
    with pytest.raises(HTTPException) as exc:
        require_instance_owner(_instancia(None), _ciudadano("cliente-2"))

    assert exc.value.status_code == 403


def test_el_id_del_ciudadano_se_compara_como_texto():
    """
    `Customer.id` es un ObjectId y `user_id` se guarda como cadena; la
    comparación tiene que normalizar o el dueño legítimo recibiría un 403.
    """

    class _ObjectIdFalso:
        def __str__(self):
            return "cliente-1"

    require_instance_owner(_instancia("cliente-1"), _ciudadano(_ObjectIdFalso()))
