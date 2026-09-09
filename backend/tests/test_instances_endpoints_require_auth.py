"""Ningún endpoint del router de instancias puede quedar sin autenticación.

`/{instance_id}/resume`, `/pause`, `/cancel`, `/approve`, `PUT /{instance_id}` y
`/analytics/bottlenecks` se declararon sin dependencia de autenticación.
Cualquiera con acceso a la API podía reanudar, pausar o cancelar el trámite de
otra persona, sobrescribir su `status` y su `context` con datos arbitrarios,
registrar una aprobación a nombre de quien quisiera, y leer el listado de
trámites atorados con sus `user_id`.

La prueba recorre la tabla de rutas y exige que cada una arrastre un esquema de
seguridad (el `HTTPBearer` de `app.auth.provider`). Es estructural a propósito:
así cubre también los endpoints que se agreguen después.
"""

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from fastapi.dependencies.utils import get_flat_dependant
from fastapi.routing import APIRoute

from app.api.endpoints import instances

# Rutas deliberadamente públicas. Vacío: todo el router opera sobre trámites de
# alguien. Agregar aquí solo con una razón escrita.
PUBLICAS: set = set()


def _rutas():
    for route in instances.router.routes:
        if isinstance(route, APIRoute):
            for metodo in sorted(route.methods - {"HEAD", "OPTIONS"}):
                yield metodo, route.path, route


def test_ninguna_ruta_de_instancias_queda_sin_autenticacion():
    sin_auth = []
    for metodo, path, route in _rutas():
        if (metodo, path) in PUBLICAS:
            continue
        if not get_flat_dependant(route.dependant).security_requirements:
            sin_auth.append(f"{metodo} {path}")

    assert not sin_auth, "endpoints sin autenticación: " + ", ".join(sorted(sin_auth))


def test_no_hay_rutas_duplicadas():
    """Una ruta declarada dos veces deja muerta a la segunda: si la primera es la
    autenticada, la insegura queda invisible en las pruebas pero sigue en el
    código esperando a que alguien reordene el archivo.
    """
    vistas = {}
    duplicadas = []
    for metodo, path, route in _rutas():
        clave = (metodo, path)
        if clave in vistas:
            duplicadas.append(f"{metodo} {path} ({vistas[clave]} y {route.endpoint.__name__})")
        else:
            vistas[clave] = route.endpoint.__name__

    assert not duplicadas, "rutas duplicadas: " + ", ".join(duplicadas)


def test_la_aprobacion_no_confia_en_el_approver_del_cuerpo():
    """El `approver_id` tiene que salir del token, no del JSON: si no, cualquiera
    firma una aprobación a nombre de otro."""
    import inspect

    fuente = inspect.getsource(instances.approve_step)
    assert "approval.approver_id = " in fuente, (
        "approve_step debe sobrescribir approver_id con el usuario autenticado"
    )
