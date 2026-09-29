#!/usr/bin/env python3
"""
Pone fecha de vencimiento a las entidades que no la tienen.

`LegalEntity.valid_until` se añadió después de que estas entidades se emitieran,
así que las existentes no la traen y el selector no puede mostrar su vigencia.
Este script la fija a un año desde su emisión: la vigencia corre desde que se
emite el documento, no desde que se corre el script.

Solo toca las que no tienen `valid_until`, así que es idempotente y no pisa una
fecha ya fijada por una resolución.

Simulación por defecto; escribe con `--aplicar`:

    docker cp backend/scripts/poblar_vigencia_entidades.py backend-conapesca:/tmp/
    docker exec backend-conapesca python /tmp/poblar_vigencia_entidades.py
    docker exec backend-conapesca python /tmp/poblar_vigencia_entidades.py --aplicar
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime

sys.path.insert(0, "/app")

from app.core.database import connect_to_mongo  # noqa: E402
from app.models.legal_entity import LegalEntity  # noqa: E402
from app.services.entity_validity import calcular_vencimiento  # noqa: E402

ANOS = 1


async def main(aplicar: bool) -> int:
    await connect_to_mongo()

    sin_fecha = await LegalEntity.find({"valid_until": None}).to_list()
    print(f"entidades sin fecha de vencimiento: {len(sin_fecha)}")

    tocadas = omitidas = vencidas = 0
    for entidad in sin_fecha:
        hasta = calcular_vencimiento(entidad.created_at, ANOS)
        if hasta is None:
            omitidas += 1
            print(f"  [omitida] {entidad.entity_id}: sin fecha de emisión utilizable")
            continue
        if hasta < datetime.utcnow():
            vencidas += 1
        if aplicar:
            entidad.valid_until = hasta
            await entidad.save()
        tocadas += 1

    print()
    modo = "APLICADO" if aplicar else "SIMULACIÓN (usa --aplicar para escribir)"
    print(f"{modo}: {tocadas} actualizadas, {omitidas} omitidas.")
    print(f"de las actualizadas, quedarían vencidas: {vencidas}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true", help="Escribe los cambios.")
    raise SystemExit(asyncio.run(main(ap.parse_args().aplicar)))
