"""Operador de generación de folio oficial de seguimiento.

El folio marca el inicio oficial de un trámite (arranca el reloj de los tiempos
de atención) y es de cara al ciudadano. Formato:

    {codigo}-{año}-{seq:04d}-{check}        p. ej.  C-T003-2026-0042-B7K

- `seq` es un consecutivo atómico por `(codigo, año)` — mismo patrón de
  `oficios/numerador.py` (Mongo `find_one_and_update` `$inc` + upsert), de modo
  que dos trámites concurrentes del mismo código/año reciban números sin
  colisión. La matriz oficial comparte prefijo entre trámites (T01 y T02 son
  ambos `C-T022`), por eso el scope del contador es prefijo+año y NO por
  workflow.
- `check` es un verificador HMAC-SHA256 derivado de `FOLIO_HMAC_SECRET`,
  codificado en base32 Crockford (mayúsculas, sin I/L/O/U). Hace el folio
  reproducible y verificable (detecta falsificación o typo) e imposible de
  adivinar sin el secreto: así el consecutivo legible deja de ser adivinable.

El operador vive en core y es agnóstico del tenant: el `prefix` se lo inyecta
quien lo instancia (en catastro, resuelto desde `oficios_catalog.yaml` por
`tramite_key`). Los trámites "SIN FOLIO" (prefijo nulo) continúan sin generar.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.config import settings
from app.core.database import get_database

from .base import BaseOperator, TaskResult, TaskStatus

logger = logging.getLogger(__name__)

COUNTERS_COLLECTION = "folio_counters"

# Alfabeto base32 Crockford: sin I, L, O, U para evitar ambigüedad al teclear.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_YEAR_SUFFIX_RE = re.compile(r"-\d{4}$")


def normalize_codigo(prefix: str) -> str:
    """Quita un año final (`-AAAA`) del prefijo del catálogo.

    El catálogo es inconsistente: unos prefijos ya traen año (`C-T003-2026`) y
    otros no (`C-T022`). Normalizarlo evita duplicar el año al construir el folio.
    """
    return _YEAR_SUFFIX_RE.sub("", (prefix or "").strip())


def _b32(data: bytes, n: int) -> str:
    """Codifica los primeros `n` símbolos base32 Crockford de `data`."""
    num = int.from_bytes(data, "big")
    out = []
    for _ in range(max(0, n)):
        out.append(_ALPHABET[num & 0x1F])
        num >>= 5
    return "".join(reversed(out))


def folio_check(base: str, secret: str, n: int = 3) -> str:
    """Verificador HMAC de `n` caracteres para la base del folio."""
    mac = hmac.new(secret.encode(), base.encode(), hashlib.sha256).digest()
    return _b32(mac, n)


def build_folio(
    codigo: str,
    year: int,
    seq: int,
    secret: str,
    seq_padding: int = 4,
    check_length: int = 3,
) -> str:
    """Construye el folio completo `{codigo}-{año}-{seq}-{check}`."""
    base = f"{codigo}-{year}-{seq:0{seq_padding}d}"
    return f"{base}-{folio_check(base, secret, check_length)}"


def verify_folio(folio: str, secret: str) -> bool:
    """¿El `check` final del folio corresponde a su base con este secreto?"""
    idx = folio.rfind("-")
    if idx <= 0:
        return False
    base, check = folio[:idx], folio[idx + 1:]
    if not check:
        return False
    return hmac.compare_digest(check, folio_check(base, secret, len(check)))


async def next_sequence(scope: str, db=None) -> int:
    """Incrementa y retorna el siguiente consecutivo para `scope` (atómico)."""
    db = db if db is not None else await get_database()
    result = await db[COUNTERS_COLLECTION].find_one_and_update(
        {"scope": scope},
        {"$inc": {"seq": 1}, "$set": {"updated_at": datetime.now(timezone.utc)}},
        upsert=True,
        return_document=True,  # pymongo.ReturnDocument.AFTER
    )
    if result is None:
        # Algunos drivers devuelven None en la primera inserción del upsert.
        result = await db[COUNTERS_COLLECTION].find_one({"scope": scope})
    seq = (result or {}).get("seq", 1)
    logger.info("Folio: scope=%s seq=%d", scope, seq)
    return seq


class FolioGeneratorOperator(BaseOperator):
    """Genera y persiste (vía context) el folio oficial de seguimiento."""

    # Paso interno: no dispara notificación propia (el folio se muestra en el
    # seguimiento; quien instancia puede forzar notify=True si lo quiere avisar).
    default_notify = False

    def __init__(
        self,
        task_id: str,
        prefix: Optional[str] = None,
        prefix_path: Optional[str] = None,
        year: Optional[int] = None,
        seq_padding: int = 4,
        check_length: int = 3,
        store_as: str = "folio_seguimiento",
        skip_if_no_prefix: bool = True,
        **kwargs,
    ):
        self.prefix = prefix
        self.prefix_path = prefix_path
        self.year = year
        self.seq_padding = seq_padding
        self.check_length = check_length
        self.store_as = store_as
        self.skip_if_no_prefix = skip_if_no_prefix
        super().__init__(task_id, **kwargs)

    def _resolve_prefix(self, context: Dict[str, Any]) -> Optional[str]:
        if self.prefix:
            return self.prefix
        if self.prefix_path:
            node: Any = context
            for part in self.prefix_path.split("."):
                node = node.get(part) if isinstance(node, dict) else None
                if node is None:
                    break
            return node
        return None

    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """Stub síncrono: el executor usa `execute_async` cuando existe."""
        return TaskResult(status=TaskStatus.WAITING, data={})

    async def execute_async(self, context: Dict[str, Any]) -> TaskResult:
        try:
            # Idempotencia: si el folio ya fue emitido, no vuelve a incrementar.
            existing = context.get(self.store_as)
            if existing:
                return TaskResult(
                    status=TaskStatus.CONTINUE,
                    data={self.store_as: existing, f"{self.task_id}_folio": existing},
                )

            prefix = self._resolve_prefix(context)
            if not prefix:
                if self.skip_if_no_prefix:
                    # Trámite SIN FOLIO: continúa sin generar.
                    return TaskResult(
                        status=TaskStatus.CONTINUE,
                        data={f"{self.task_id}_sin_folio": True},
                    )
                return TaskResult(
                    status=TaskStatus.FAILED,
                    error="No se resolvió el prefijo del folio y skip_if_no_prefix=False",
                )

            secret = getattr(settings, "FOLIO_HMAC_SECRET", None)
            if not secret:
                return TaskResult(
                    status=TaskStatus.FAILED,
                    error=(
                        "FOLIO_HMAC_SECRET no configurado: no se puede emitir un "
                        "folio verificable (se evita emitir uno adivinable)"
                    ),
                )

            codigo = normalize_codigo(str(prefix))
            year = self.year or datetime.now(timezone.utc).year
            scope = f"{codigo}-{year}"
            seq = await next_sequence(scope)
            folio = build_folio(
                codigo,
                year,
                seq,
                secret,
                seq_padding=self.seq_padding,
                check_length=self.check_length,
            )
            return TaskResult(
                status=TaskStatus.CONTINUE,
                data={self.store_as: folio, f"{self.task_id}_folio": folio},
            )
        except Exception as exc:  # pragma: no cover - defensivo
            logger.exception("FolioGeneratorOperator falló: %s", exc)
            return TaskResult(status=TaskStatus.FAILED, error=str(exc))
