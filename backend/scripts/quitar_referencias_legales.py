#!/usr/bin/env python3
"""
Quita las citas legales de la ficha del trámite; deja el costo.

En la ficha pública, junto al monto, aparecía un campo con el articulado:

    Referencia de costo · Art. 191-A fr. I Ley Federal de Derechos (…)

Las áreas operativas pidieron dejar **solo el costo**. La cita no le sirve al
ciudadano para decidir si hace el trámite, ocupa el mismo espacio que el dato que
sí importa, y envejece: cuando la Ley Federal de Derechos se reforma, el monto se
actualiza desde su propia fuente pero la cita se queda escrita a mano y miente.

El monto **no se toca**: vive en `metadata.cost`, no en estos campos.

Se van las etiquetas de `ETIQUETAS_A_QUITAR`. **"Vigencia" se queda**: no es una
cita legal, es información que el ciudadano necesita ("2 años").

Estos campos se capturaron a mano desde el editor del admin —ningún script los
escribe—, así que esto es una limpieza de una sola vez; idempotente, porque
filtra por etiqueta y volver a correrlo no encuentra nada.

Simulación por defecto; escribe con `--aplicar`:

    docker cp backend/scripts/quitar_referencias_legales.py backend-conapesca:/tmp/
    docker exec backend-conapesca python /tmp/quitar_referencias_legales.py
    docker exec backend-conapesca python /tmp/quitar_referencias_legales.py --aplicar
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

ETIQUETAS_A_QUITAR = {
    "Referencia de costo",
    "Fundamento legal",
}


async def main(aplicar: bool) -> int:
    await connect_to_mongo()
    col = (await get_database())["workflow_definitions"]

    definiciones = await col.find(
        {"metadata.customFields": {"$exists": True, "$ne": []}},
        {"workflow_id": 1, "metadata": 1},
    ).to_list(length=None)
    print(f"definiciones con campos personalizados: {len(definiciones)}")

    # Estos textos se escribieron a mano en el editor del admin: no hay fuente de
    # la que regenerarlos. Se deja copia antes de borrarlos.
    respaldo: list[dict] = []
    pendientes: list[tuple] = []

    tocadas = quitados = 0
    for d in definiciones:
        metadata = d.get("metadata") or {}
        campos = metadata.get("customFields") or []

        se_quedan = [c for c in campos if c.get("label") not in ETIQUETAS_A_QUITAR]
        se_van = [c for c in campos if c.get("label") in ETIQUETAS_A_QUITAR]
        if not se_van:
            continue

        tocadas += 1
        quitados += len(se_van)
        respaldo.append({"workflow_id": d.get("workflow_id"), "customFields": campos})
        for c in se_van:
            valor = str(c.get("value", ""))
            recorte = valor if len(valor) <= 70 else valor[:67] + "…"
            print(f"  {d['workflow_id']}: quitar {c.get('label')!r} = {recorte!r}")
        if not se_quedan:
            print("    (queda sin campos personalizados)")

        pendientes.append((d["_id"], se_quedan))

    # El respaldo se escribe **antes** de tocar nada: si el guardado falla, no se
    # ha borrado todavía.
    if aplicar and respaldo:
        destino = Path("/tmp") / f"customfields_respaldo_{datetime.utcnow():%Y%m%d_%H%M%S}.json"
        destino.write_text(json.dumps(respaldo, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nrespaldo escrito en {destino}")
        for _id, se_quedan in pendientes:
            # Se conserva la clave con lista vacía en vez de borrarla: el editor del
            # admin la espera y una clave ausente lo obliga a reconstruirla.
            await col.update_one({"_id": _id}, {"$set": {"metadata.customFields": se_quedan}})

    conservados = sum(
        1
        for d in definiciones
        for c in ((d.get("metadata") or {}).get("customFields") or [])
        if c.get("label") not in ETIQUETAS_A_QUITAR
    )
    print(
        f"\ndefiniciones afectadas: {tocadas} | campos quitados: {quitados} | "
        f"campos conservados: {conservados}"
    )
    if not aplicar:
        print("\nSIMULACIÓN — no se escribió nada. Vuelve a correrlo con --aplicar.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--aplicar",
        action="store_true",
        help="escribe los cambios (sin esto solo simula)",
    )
    raise SystemExit(asyncio.run(main(parser.parse_args().aplicar)))
