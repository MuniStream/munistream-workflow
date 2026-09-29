#!/usr/bin/env python3
"""
Repara las entidades cuyo nombre quedó siendo una variable sin expandir.

En la cartera del ciudadano aparecen documentos llamados así:

    _selected_entities_data.embarcacion_ids.0.nombre

Es el `name_source` del trámite tal cual: la ruta apuntaba a un campo que la
entidad seleccionada no trae —el nombre de la embarcación se guarda en
`nombre_embarcacion`— y, al no resolver, el nombre se quedó con la ruta literal.

El código ya no puede producirlos (`services/entity_naming.py`) y las rutas de los
trámites están corregidas, pero las entidades emitidas antes siguen con el nombre
roto: el ciudadano las ve así en su cartera hoy.

**Cómo se recupera el nombre de verdad, en vez de poner una etiqueta genérica.**
El nombre roto *es* la ruta que se quiso usar, y la instancia que creó la entidad
conserva su contexto. Así que se vuelve a resolver contra ese contexto, ahora
mirando también los campos que sí existen en ese nodo. Solo cuando no queda nada
se cae al nombre legible del tipo.

Simulación por defecto; escribe con `--aplicar`:

    docker cp backend/scripts/reparar_nombres_de_entidades.py backend-conapesca:/tmp/
    docker exec backend-conapesca python /tmp/reparar_nombres_de_entidades.py
    docker exec backend-conapesca python /tmp/reparar_nombres_de_entidades.py --aplicar
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/app")

from app.core.database import connect_to_mongo, get_database  # noqa: E402
from app.services.entity_naming import (  # noqa: E402
    CAMPOS_PARA_NOMBRAR,
    _extraer_por_defecto,
    hay_sin_resolver,
    nombre_legible_de_tipo,
)

# Campos del *snapshot* de una entidad seleccionada que sirven para nombrarla.
# Se antepone el campo que la ruta original buscaba; después, los que de verdad
# existen en esos nodos, medidos sobre la base. `_entity_name` va al final: lo
# inyecta el selector y está siempre, así que siempre resuelve algo.
CAMPOS_DEL_SNAPSHOT = (
    "nombre_embarcacion",
    "zona_impacto",
    "embarcacion",
    *CAMPOS_PARA_NOMBRAR,
    "_entity_name",
)


def _nombre_desde_el_contexto(ruta_rota: str, contexto: dict) -> str | None:
    """
    Recupera el nombre que se quiso poner, a partir de la ruta que falló.

    De `_selected_entities_data.embarcacion_ids.0.nombre` se toma el nodo
    `_selected_entities_data.embarcacion_ids.0` y se busca en él un campo que sí
    nombre la entidad.
    """
    if "." not in ruta_rota:
        return None

    nodo = _extraer_por_defecto(contexto, ruta_rota.rsplit(".", 1)[0])
    if not isinstance(nodo, dict):
        return None

    for campo in CAMPOS_DEL_SNAPSHOT:
        valor = nodo.get(campo)
        if isinstance(valor, str) and valor.strip() and not hay_sin_resolver(valor):
            return valor.strip()
    return None


async def main(aplicar: bool) -> int:
    await connect_to_mongo()
    db = await get_database()
    entidades = db["legal_entities"]
    instancias = db["workflow_instances"]

    rotas = [
        d
        async for d in entidades.find({}, {"entity_id": 1, "entity_type": 1, "name": 1,
                                           "data": 1, "created_by_workflow": 1})
        if hay_sin_resolver(d.get("name"))
    ]
    print(f"entidades con el nombre sin expandir: {len(rotas)}")

    respaldo = []
    pendientes = []
    for d in rotas:
        roto = d["name"]

        # 1. El contexto de la instancia que la creó: ahí está el dato real.
        nuevo = None
        origen = "contexto de la instancia"
        iid = d.get("created_by_workflow")
        if iid:
            inst = await instancias.find_one({"instance_id": iid}, {"context": 1})
            nuevo = _nombre_desde_el_contexto(roto, (inst or {}).get("context") or {})

        # 2. Los datos de la propia entidad.
        if not nuevo:
            origen = "datos de la entidad"
            datos = d.get("data") or {}
            for campo in CAMPOS_PARA_NOMBRAR:
                valor = datos.get(campo)
                if isinstance(valor, str) and valor.strip() and not hay_sin_resolver(valor):
                    nuevo = valor.strip()
                    break

        # 3. El tipo, legible. Pierde especificidad, pero no miente ni confunde.
        if not nuevo:
            origen = "nombre legible del tipo"
            nuevo = nombre_legible_de_tipo(d.get("entity_type"))

        print(f"  {d['entity_id']}")
        print(f"      antes : {roto}")
        print(f"      ahora : {nuevo}   ({origen})")
        respaldo.append({"entity_id": d["entity_id"], "name": roto})
        pendientes.append((d["_id"], nuevo))

    if aplicar and pendientes:
        # El nombre viejo no se puede regenerar, así que se guarda antes de tocar.
        destino = Path("/tmp") / f"nombres_respaldo_{datetime.utcnow():%Y%m%d_%H%M%S}.json"
        destino.write_text(json.dumps(respaldo, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nrespaldo escrito en {destino}")
        for _id, nuevo in pendientes:
            await entidades.update_one(
                {"_id": _id}, {"$set": {"name": nuevo, "updated_at": datetime.utcnow()}}
            )
        print(f"entidades renombradas: {len(pendientes)}")
    elif not aplicar:
        print("\nSIMULACIÓN — no se escribió nada. Vuelve a correrlo con --aplicar.")

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aplicar", action="store_true", help="escribe los cambios")
    raise SystemExit(asyncio.run(main(parser.parse_args().aplicar)))
