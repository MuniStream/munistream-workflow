"""
Los pickers no ofrecen entidades inactivas.

Un hook de sincronización (LlaveMX, RNPA) marca `status = "inactivo"` lo que su
fuente dejó de reportar, sin borrarlo. Los pickers buscaban solo por dueño, tipo
y `filters`, así que seguían ofreciendo esas entidades en los trámites. Ahora
`EntityService.find_entities` y `construir_consulta` las excluyen cuando se les
pide (`excluir_inactivas=True`), que es lo que hacen el `EntityPickerOperator` y
el listado de candidatas del portal. Los listados del ciudadano y del admin
siguen mostrándolas.

Corre contra la Mongo del contenedor del backend:

    docker exec backend-conapesca python -m pytest /app/tests/test_pickers_no_ofrecen_inactivas.py -q
"""

import asyncio
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.core.database import connect_to_mongo  # noqa: E402
from app.models.legal_entity import ENTITY_STATUS_INACTIVE, LegalEntity  # noqa: E402
from app.services.entity_search import construir_consulta  # noqa: E402
from app.services.entity_service import EntityService  # noqa: E402

DUENO = "prueba-pickers-no-ofrecen-inactivas"


async def _con_entidades(prueba):
    await connect_to_mongo()
    entidades = [
        LegalEntity(entity_id=f"{DUENO}-{status}", entity_type="permiso_pesca_comercial",
                    owner_user_id=DUENO, name=status, status=status,
                    data={"entity_subtype": "rnpa"})
        for status in ("vigente", ENTITY_STATUS_INACTIVE)
    ]
    for e in entidades:
        await e.insert()
    try:
        await prueba()
    finally:
        await LegalEntity.find({"owner_user_id": DUENO}).delete()


def test_find_entities_excluye_inactivas_solo_si_se_pide():
    async def prueba():
        todas = await EntityService.find_entities(owner_user_id=DUENO, entity_type="permiso_pesca_comercial")
        activas = await EntityService.find_entities(
            owner_user_id=DUENO, entity_type="permiso_pesca_comercial",
            filters={"entity_subtype": ["permiso_simplificado", "rnpa"]}, excluir_inactivas=True,
        )
        assert sorted(e.status for e in todas) == sorted(["vigente", ENTITY_STATUS_INACTIVE])
        assert [e.status for e in activas] == ["vigente"]

    asyncio.run(_con_entidades(prueba))


def test_construir_consulta_excluye_inactivas_salvo_status_explicito():
    async def prueba():
        consulta = construir_consulta(owner_user_id=DUENO, entity_type="permiso_pesca_comercial",
                                      filtros={"entity_subtype": ["permiso_simplificado", "rnpa"]},
                                      excluir_inactivas=True)
        assert [e.status for e in await LegalEntity.find(consulta).to_list()] == ["vigente"]

        explicita = construir_consulta(owner_user_id=DUENO, status=ENTITY_STATUS_INACTIVE,
                                       excluir_inactivas=True)
        assert [e.status for e in await LegalEntity.find(explicita).to_list()] == [ENTITY_STATUS_INACTIVE]

    asyncio.run(_con_entidades(prueba))
