"""
El perfil Llave MX guarda el nombre y cada apellido por separado.

Llave MX los entrega separados (`datosUsuario`: nombre, primerApellido,
segundoApellido) y el shim los propaga como `given_name`, `primer_apellido` y
`segundo_apellido`. `family_name` los junta con un espacio, y con un apellido
compuesto ("San Martín Dávalos") ya no se sabe dónde termina el paterno. El
tenant los necesita separados para calcular el RFC de la persona física con el
algoritmo del SAT.

    docker exec backend-conapesca python -m pytest /app/tests/test_llavemx_nombre_y_apellidos.py -q
"""

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.api.endpoints.public_auth import _sync_llavemx_profile  # noqa: E402
from app.models.customer import Customer, CustomerStatus  # noqa: E402

CURP = "PEVA900101HDFXXX01"


def test_nombre_y_apellidos_se_guardan_por_separado():
    customer = Customer.model_construct(
        email="prueba-nombre@example.com", full_name="Prueba", phone="", document_number=CURP,
        password_hash="", status=CustomerStatus.ACTIVE, curp=CURP, rfc=None,
        tipo_persona="fisica", llavemx_user_id="1", metadata={},
    )
    _sync_llavemx_profile(customer, {"curp": CURP, "given_name": "Luz María",
                                     "primer_apellido": "San Martín", "segundo_apellido": "Dávalos"})
    llavemx = customer.metadata["llavemx"]
    assert (llavemx["nombre"], llavemx["primer_apellido"], llavemx["segundo_apellido"]) == \
        ("Luz María", "San Martín", "Dávalos")
