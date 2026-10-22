"""
Un token sin el claim `personas_morales` no borra las que ya se conocían.

`_sync_llavemx_profile` reescribía `metadata["llavemx"]["personas_morales"]`
con `[]` ante cualquier token sin el claim, y el hook del tenant inactivaba en
consecuencia las entidades de persona moral del ciudadano. La ausencia del claim
no dice que ya no las tenga (puede faltar el mapper en un client); un claim
presente y vacío, sí.

    docker exec backend-conapesca python -m pytest /app/tests/test_llavemx_personas_morales_sin_claim.py -q
"""

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.api.endpoints.public_auth import _sync_llavemx_profile  # noqa: E402
from app.models.customer import Customer, CustomerStatus  # noqa: E402

MORAL = {"rfc": "ABC010101AB1", "razonSocial": "Pesquera Uno SA de CV"}
CURP = "PEVA900101HDFXXX01"


def _customer(personas_morales):
    return Customer.model_construct(
        email="prueba-pm@example.com", full_name="Prueba", phone="", document_number=CURP,
        password_hash="", status=CustomerStatus.ACTIVE, curp=CURP, rfc=None,
        tipo_persona="fisica", llavemx_user_id="1",
        metadata={"llavemx": {"personas_morales": personas_morales}},
    )


def test_sin_claim_se_conservan_las_personas_morales():
    customer = _customer([MORAL])
    _sync_llavemx_profile(customer, {"curp": CURP})
    assert customer.metadata["llavemx"]["personas_morales"] == [MORAL]


def test_claim_vacio_si_las_quita():
    customer = _customer([MORAL])
    _sync_llavemx_profile(customer, {"curp": CURP, "personas_morales": "[]"})
    assert customer.metadata["llavemx"]["personas_morales"] == []


def test_claim_presente_las_reemplaza():
    customer = _customer([])
    _sync_llavemx_profile(customer, {"personas_morales": '[{"rfc": "XYZ"}]'})
    assert customer.metadata["llavemx"]["personas_morales"] == [{"rfc": "XYZ"}]
