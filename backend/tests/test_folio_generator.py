"""Operador de generación de folio oficial de seguimiento.

El folio arranca el reloj de los tiempos oficiales de atención y es de cara al
ciudadano. Formato: `{codigo}-{año}-{seq:04d}-{check}` (p. ej.
`C-T003-2026-0042-B7K`). El `seq` es un consecutivo atómico por (codigo, año);
el `check` es un verificador HMAC derivado de un secreto, de modo que el folio
sea reproducible y verificable, pero imposible de adivinar sin el secreto.

Prefijo y año salen del catálogo de catastro (`folio_prefix` por `tramite_key`);
el operador vive en core y es agnóstico del tenant.
"""

import asyncio
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.core.config import settings
from app.workflows.operators.base import TaskStatus
from app.workflows.operators import folio_generator as fg
from app.workflows.operators.folio_generator import (
    FolioGeneratorOperator,
    build_folio,
    folio_check,
    normalize_codigo,
    verify_folio,
)

SECRET = "test-folio-secret"


# --- Helpers puros (sin Mongo ni settings) ---------------------------------

def test_build_folio_formato_y_padding():
    folio = build_folio("C-T003", 2026, 42, SECRET)
    # C-T003-2026-0042-XXX  (consecutivo a 4 dígitos + check de 3 chars)
    assert folio.startswith("C-T003-2026-0042-")
    base, check = folio.rsplit("-", 1)
    assert base == "C-T003-2026-0042"
    assert len(check) == 3
    # alfabeto Crockford en mayúsculas, sin I/L/O/U
    assert all(c in "0123456789ABCDEFGHJKMNPQRSTVWXYZ" for c in check)


def test_folio_check_determinista():
    a = folio_check("C-T003-2026-0042", SECRET, 3)
    b = folio_check("C-T003-2026-0042", SECRET, 3)
    assert a == b
    # cambia con el secreto
    assert folio_check("C-T003-2026-0042", "otro-secreto", 3) != a


def test_verify_folio_acepta_valido_y_rechaza_alterado():
    folio = build_folio("C-T003", 2026, 42, SECRET)
    assert verify_folio(folio, SECRET) is True
    # typo en el consecutivo: 0042 -> 0043
    alterado_seq = folio.replace("0042", "0043")
    assert verify_folio(alterado_seq, SECRET) is False
    # typo en el verificador
    base, check = folio.rsplit("-", 1)
    otro = "A" if check[0] != "A" else "B"
    alterado_check = f"{base}-{otro}{check[1:]}"
    assert verify_folio(alterado_check, SECRET) is False
    # otro secreto no valida
    assert verify_folio(folio, "secreto-equivocado") is False


def test_normalize_codigo_recorta_año_final():
    # el catálogo es inconsistente: unos prefijos traen año, otros no.
    assert normalize_codigo("C-T003-2026") == "C-T003"
    assert normalize_codigo("C-T022") == "C-T022"


def test_build_folio_no_duplica_año_con_prefijo_que_ya_lo_trae():
    folio = build_folio(normalize_codigo("C-T003-2026"), 2026, 7, SECRET)
    assert folio.startswith("C-T003-2026-0007-")
    assert "2026-2026" not in folio


# --- Operador (async) con contador e integración monkeypatcheados ----------

def _run(coro):
    return asyncio.run(coro)


def test_operador_genera_folio_y_continua(monkeypatch):
    monkeypatch.setattr(settings, "FOLIO_HMAC_SECRET", SECRET, raising=False)

    async def fake_seq(scope, db=None):
        assert scope == "C-T003-2026"
        return 42
    monkeypatch.setattr(fg, "next_sequence", fake_seq)

    op = FolioGeneratorOperator(task_id="generar_folio", prefix="C-T003-2026", year=2026)
    res = _run(op.execute_async({"instance_id": "i1"}))

    assert res.status == TaskStatus.CONTINUE
    assert res.data["folio_seguimiento"] == "C-T003-2026-0042-" + folio_check("C-T003-2026-0042", SECRET, 3)
    assert res.data["generar_folio_folio"] == res.data["folio_seguimiento"]


def test_operador_sin_prefijo_continua_sin_folio(monkeypatch):
    monkeypatch.setattr(settings, "FOLIO_HMAC_SECRET", SECRET, raising=False)
    op = FolioGeneratorOperator(task_id="generar_folio", prefix=None)
    res = _run(op.execute_async({"instance_id": "i1"}))
    assert res.status == TaskStatus.CONTINUE
    assert "folio_seguimiento" not in (res.data or {})


def test_operador_falla_si_no_hay_secreto(monkeypatch):
    monkeypatch.setattr(settings, "FOLIO_HMAC_SECRET", None, raising=False)

    async def fake_seq(scope, db=None):
        return 1
    monkeypatch.setattr(fg, "next_sequence", fake_seq)

    op = FolioGeneratorOperator(task_id="generar_folio", prefix="C-T003-2026")
    res = _run(op.execute_async({"instance_id": "i1"}))
    assert res.status == TaskStatus.FAILED
    assert res.error


def test_operador_es_idempotente(monkeypatch):
    monkeypatch.setattr(settings, "FOLIO_HMAC_SECRET", SECRET, raising=False)

    llamadas = {"n": 0}

    async def fake_seq(scope, db=None):
        llamadas["n"] += 1
        return 5
    monkeypatch.setattr(fg, "next_sequence", fake_seq)

    op = FolioGeneratorOperator(task_id="generar_folio", prefix="C-T003-2026", year=2026)
    ctx = {"instance_id": "i1"}
    r1 = _run(op.execute_async(ctx))
    # simula que el context ya tiene el folio persistido
    ctx["folio_seguimiento"] = r1.data["folio_seguimiento"]
    r2 = _run(op.execute_async(ctx))

    assert r1.data["folio_seguimiento"] == r2.data["folio_seguimiento"]
    assert llamadas["n"] == 1  # no volvió a incrementar el contador


# --- Integración real contra Mongo: el contador atómico --------------------
# Conexión directa con motor (como el test de S3 usa boto3 directo): no depende
# de que Beanie esté inicializado en el proceso de pytest.

import uuid


def test_next_sequence_incrementa_atomico_por_scope():
    # Todo en un solo loop: motor liga el cliente al loop donde se crea/usa.
    scope_a = "ITEST-" + uuid.uuid4().hex[:10]
    scope_b = "ITEST-" + uuid.uuid4().hex[:10]

    async def run():
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(settings.MONGODB_URL, serverSelectionTimeoutMS=1500)
        try:
            await client.admin.command("ping")
        except Exception:
            client.close()
            return None
        db = client[settings.MONGODB_DB_NAME]
        try:
            s1 = await fg.next_sequence(scope_a, db=db)
            s2 = await fg.next_sequence(scope_a, db=db)
            s3 = await fg.next_sequence(scope_a, db=db)
            sb = await fg.next_sequence(scope_b, db=db)
            await db[fg.COUNTERS_COLLECTION].delete_many({"scope": {"$in": [scope_a, scope_b]}})
            return s1, s2, s3, sb
        finally:
            client.close()

    out = asyncio.run(run())
    if out is None:
        import pytest
        pytest.skip("Mongo no disponible")
    s1, s2, s3, sb = out
    assert (s1, s2, s3) == (1, 2, 3)   # consecutivo bajo el mismo scope
    assert sb == 1                     # scope distinto no se mezcla
