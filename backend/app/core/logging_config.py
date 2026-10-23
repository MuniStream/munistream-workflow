"""
Enhanced logging configuration with GELF support for structured workflow logging.
Extends existing Python logging to automatically include workflow context.
"""

import logging
import json
import re
import socket
import time
from typing import Optional, Dict, Any
from contextvars import ContextVar

# Context variables for workflow data
current_user_id: ContextVar[Optional[str]] = ContextVar('current_user_id', default=None)
current_workflow_id: ContextVar[Optional[str]] = ContextVar('current_workflow_id', default=None)
current_instance_id: ContextVar[Optional[str]] = ContextVar('current_instance_id', default=None)
current_tenant: ContextVar[Optional[str]] = ContextVar('current_tenant', default=None)
current_step: ContextVar[Optional[str]] = ContextVar('current_step', default=None)

# Mexican personal identifiers. The citizen's own login file keeps them in full
# (it is their record); the shared outputs (console, Graylog) show only the
# first four characters, enough to tell records apart without exposing them.
_CURP_RE = re.compile(r"\b[A-Z][AEIOUX][A-Z]{2}\d{6}[HMX][A-Z]{5}[A-Z0-9]\d\b")
_RFC_RE = re.compile(r"\b[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}\b")


def mask_personal_ids(text: str) -> str:
    """Mask CURPs and RFCs in ``text`` to their first four characters."""
    if not text:
        return text
    masked = _CURP_RE.sub(lambda m: m.group(0)[:4] + "…", text)
    return _RFC_RE.sub(lambda m: m.group(0)[:4] + "…", masked)


def _login_run() -> Optional[str]:
    # Imported lazily: login_log has no dependency on this module and must stay so.
    from .login_log import current_login_run
    return current_login_run.get()


class MaskingConsoleFormatter(logging.Formatter):
    """Console formatter: tags login records with their run id, masks personal ids."""

    def format(self, record):
        record.login_tag = f" [login {_login_run()}]" if _login_run() else ""
        return mask_personal_ids(super().format(record))


class GELFFormatter(logging.Formatter):
    """Custom formatter that creates GELF-compatible JSON messages with workflow context."""

    def __init__(self, graylog_host: str = "graylog", graylog_port: int = 12201, container_name: str = None):
        super().__init__()
        self.graylog_host = graylog_host
        self.graylog_port = graylog_port
        self.hostname = socket.gethostname()
        self.container_name = container_name

    def format(self, record):
        # Create GELF message
        gelf_message = {
            "version": "1.1",
            "host": self.hostname,
            "short_message": mask_personal_ids(record.getMessage()),
            "timestamp": record.created,
            "level": self._level_to_gelf(record.levelno),
            "facility": "munistream-workflow",
            "_logger": record.name,
            "_filename": record.filename,
            "_line": record.lineno,
            "_thread": record.thread,
        }

        # Add container name if provided
        if self.container_name:
            gelf_message["container_name"] = self.container_name

        # Add workflow context if available
        if current_user_id.get():
            gelf_message["_user_id"] = current_user_id.get()
        if current_workflow_id.get():
            gelf_message["_workflow_id"] = current_workflow_id.get()
        if current_instance_id.get():
            gelf_message["_instance_id"] = current_instance_id.get()
        if current_tenant.get():
            gelf_message["_tenant"] = current_tenant.get()
        if current_step.get():
            gelf_message["_step"] = current_step.get()
        if _login_run():
            gelf_message["_login_run"] = _login_run()

        # Add any extra fields from record
        for key, value in record.__dict__.items():
            if key.startswith('workflow_') or key.startswith('user_') or key.startswith('step_'):
                field_name = f"_{key}" if not key.startswith('_') else key
                gelf_message[field_name] = str(value)

        # Add extra fields passed via logging extra parameter
        if hasattr(record, 'extra') and record.extra:
            for key, value in record.extra.items():
                field_name = f"_{key}" if not key.startswith('_') else key
                gelf_message[field_name] = str(value)

        # Add exception info if present
        if record.exc_info:
            gelf_message["_exception"] = mask_personal_ids(self.formatException(record.exc_info))

        return json.dumps(gelf_message)

    def _level_to_gelf(self, level):
        """Convert Python log level to GELF level."""
        mapping = {
            logging.DEBUG: 7,      # Debug
            logging.INFO: 6,       # Informational
            logging.WARNING: 4,    # Warning
            logging.ERROR: 3,      # Error
            logging.CRITICAL: 2    # Critical
        }
        return mapping.get(level, 6)


class GELFHandler(logging.Handler):
    """Handler that sends GELF messages directly to Graylog via UDP."""

    def __init__(self, graylog_host: str = "graylog", graylog_port: int = 12201, container_name: str = None):
        super().__init__()
        self.graylog_host = graylog_host
        self.graylog_port = graylog_port
        self.setFormatter(GELFFormatter(graylog_host, graylog_port, container_name))

    def emit(self, record):
        try:
            # Format the record into GELF JSON
            gelf_json = self.format(record)

            # Send to Graylog via UDP
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(gelf_json.encode('utf-8'), (self.graylog_host, self.graylog_port))
            sock.close()

        except Exception:
            # Silently fail - don't break application if Graylog is down
            pass


# Helper functions to manage workflow context
def set_workflow_context(
    user_id: str = None,
    workflow_id: str = None,
    instance_id: str = None,
    tenant: str = None,
    step: str = None
):
    """Set workflow context for subsequent log messages."""
    if user_id is not None:
        current_user_id.set(user_id)
    if workflow_id is not None:
        current_workflow_id.set(workflow_id)
    if instance_id is not None:
        current_instance_id.set(instance_id)
    if tenant is not None:
        current_tenant.set(tenant)
    if step is not None:
        current_step.set(step)


def clear_workflow_context():
    """Clear all workflow context."""
    current_user_id.set(None)
    current_workflow_id.set(None)
    current_instance_id.set(None)
    current_tenant.set(None)
    current_step.set(None)


def get_workflow_context() -> Dict[str, Optional[str]]:
    """Get current workflow context."""
    return {
        "user_id": current_user_id.get(),
        "workflow_id": current_workflow_id.get(),
        "instance_id": current_instance_id.get(),
        "tenant": current_tenant.get(),
        "step": current_step.get()
    }


# Keyword arguments that belong to Logger.log() itself, not to the record's
# extra fields.
_LOGGING_KWARGS = ("exc_info", "stack_info", "stacklevel")

# Attributes every LogRecord already has; Logger.makeRecord() raises KeyError
# if an extra field tries to overwrite one of them.
_RESERVED_RECORD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime"}


def _log(logger: logging.Logger, level: int, message, fields: Dict[str, Any]) -> None:
    """Log with context fields as extra, passing logging keywords through.

    Context fields that collide with LogRecord attributes are kept under an
    ``extra_`` prefix: a log call must never raise, least of all from an
    ``except`` block, where the error would replace the one being reported.
    """
    log_kwargs = {key: fields.pop(key) for key in _LOGGING_KWARGS if key in fields}
    extra = {
        (f"extra_{key}" if key in _RESERVED_RECORD_ATTRS else key): value
        for key, value in fields.items()
    }
    # One frame for this helper and one for the wrapper method, so the record
    # points at the caller.
    log_kwargs["stacklevel"] = log_kwargs.get("stacklevel", 1) + 2
    logger.log(level, message, extra=extra, **log_kwargs)


class WorkflowContextLogger:
    """Wrapper around standard logger that automatically sets context."""

    def __init__(self, name: str):
        self.logger = logging.getLogger(name)

    def with_context(self, **context):
        """Temporarily set context for a series of log calls."""
        old_context = get_workflow_context()
        set_workflow_context(**context)
        return ContextualLogger(self.logger, old_context)

    def info(self, message, /, **extra):
        _log(self.logger, logging.INFO, message, extra)

    def debug(self, message, /, **extra):
        _log(self.logger, logging.DEBUG, message, extra)

    def warning(self, message, /, **extra):
        _log(self.logger, logging.WARNING, message, extra)

    def error(self, message, /, **extra):
        _log(self.logger, logging.ERROR, message, extra)

    def critical(self, message, /, **extra):
        _log(self.logger, logging.CRITICAL, message, extra)

    def exception(self, message, /, **extra):
        extra.setdefault("exc_info", True)
        _log(self.logger, logging.ERROR, message, extra)


class ContextualLogger:
    """Context manager for temporary workflow context."""

    def __init__(self, logger, old_context):
        self.logger = logger
        self.old_context = old_context

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Restore old context
        set_workflow_context(**self.old_context)

    def info(self, message, /, **extra):
        _log(self.logger, logging.INFO, message, extra)

    def debug(self, message, /, **extra):
        _log(self.logger, logging.DEBUG, message, extra)

    def warning(self, message, /, **extra):
        _log(self.logger, logging.WARNING, message, extra)

    def error(self, message, /, **extra):
        _log(self.logger, logging.ERROR, message, extra)

    def critical(self, message, /, **extra):
        _log(self.logger, logging.CRITICAL, message, extra)

    def exception(self, message, /, **extra):
        extra.setdefault("exc_info", True)
        _log(self.logger, logging.ERROR, message, extra)


def setup_gelf_logging(graylog_host: str = "graylog", graylog_port: int = 12201, container_name: str = None):
    """Setup GELF logging for all loggers."""

    # Create GELF handler
    gelf_handler = GELFHandler(graylog_host, graylog_port, container_name)
    gelf_handler.setLevel(logging.DEBUG)

    # Add to root logger
    root_logger = logging.getLogger()
    root_logger.addHandler(gelf_handler)

    # Ensure we don't lose console output
    if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(MaskingConsoleFormatter(
            '%(asctime)s - %(name)s - %(levelname)s -%(login_tag)s %(message)s'
        ))
        root_logger.addHandler(console_handler)

    root_logger.setLevel(logging.INFO)


# Convenience function to get workflow-aware logger
def get_workflow_logger(name: str) -> WorkflowContextLogger:
    """Get a workflow-aware logger instance."""
    return WorkflowContextLogger(name)