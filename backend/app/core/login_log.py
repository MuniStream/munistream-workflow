"""
Bitácora del inicio de sesión de cada ciudadano, en ``<LOGIN_LOG_DIR>/<user_id>.log``.

Lo que pasa al iniciar sesión (claims del token, perfil guardado, callbacks
post-auth del tenant y las tareas que esos callbacks dejan en segundo plano) se
escribe, además de en la bitácora general, en un archivo propio del ciudadano.
El ``user_id`` es el id del Customer, el mismo ``owner_user_id`` de sus
entidades.

``login_session(user_id)`` marca el contexto: todo registro emitido dentro de
él, por cualquier logger, llega al archivo de ese ciudadano. Las tareas de
asyncio creadas dentro copian el contexto, así que también quedan en el archivo.
Fuera de un inicio de sesión el handler no escribe nada.

Each login also gets a short run id (``login_run``) that is stamped on every
record emitted inside it, in the citizen's file and in the general logs alike,
so the lines of one login (and of the background work it started) can be told
apart from the previous ones.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional
import logging
import os
import re
import threading
import uuid

current_login_user: ContextVar[Optional[str]] = ContextVar("current_login_user", default=None)
current_login_run: ContextVar[Optional[str]] = ContextVar("current_login_run", default=None)

_NOMBRE_SEGURO = re.compile(r"[^A-Za-z0-9_-]")


class LoginLogHandler(logging.Handler):
    """Escribe en el archivo del ciudadano cuyo inicio de sesión está en curso."""

    def __init__(self, directorio: str):
        super().__init__(level=logging.DEBUG)
        self.directorio = directorio
        self._lock_archivos = threading.Lock()
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [run %(login_run)s] %(name)s: %(message)s"))

    def ruta(self, user_id: str) -> str:
        return os.path.join(self.directorio, f"{_NOMBRE_SEGURO.sub('_', user_id)}.log")

    def emit(self, record: logging.LogRecord) -> None:
        user_id = current_login_user.get()
        if not user_id:
            return
        try:
            if not hasattr(record, "login_run"):
                record.login_run = current_login_run.get() or "-"
            linea = self.format(record)
            with self._lock_archivos:
                os.makedirs(self.directorio, exist_ok=True)
                with open(self.ruta(user_id), "a", encoding="utf-8") as archivo:
                    archivo.write(linea + "\n")
        except Exception:
            self.handleError(record)


def install_login_log(directorio: Optional[str]) -> Optional[LoginLogHandler]:
    """Agrega el handler al logger raíz; sin directorio no hace nada."""
    if not directorio:
        return None
    raiz = logging.getLogger()
    for handler in raiz.handlers:
        if isinstance(handler, LoginLogHandler) and handler.directorio == directorio:
            return handler
    handler = LoginLogHandler(directorio)
    raiz.addHandler(handler)
    return handler


def login_run_id() -> Optional[str]:
    """Run id of the login in progress, or None outside a login."""
    return current_login_run.get()


@contextmanager
def login_session(user_id: str) -> Iterator[str]:
    """Mark the login of ``user_id``; yields the run id stamped on its records."""
    run_id = uuid.uuid4().hex[:8]
    token = current_login_user.set(user_id)
    run_token = current_login_run.set(run_id)
    try:
        yield run_id
    finally:
        current_login_run.reset(run_token)
        current_login_user.reset(token)
