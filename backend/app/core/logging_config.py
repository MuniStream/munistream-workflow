"""
Enhanced logging configuration with GELF support for structured workflow logging.
Extends existing Python logging to automatically include workflow context.
"""

import logging
import json
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
            "short_message": record.getMessage(),
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
            gelf_message["_exception"] = self.formatException(record.exc_info)

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


class WorkflowContextLogger:
    """Wrapper around standard logger that automatically sets context."""

    def __init__(self, name: str):
        self.logger = logging.getLogger(name)

    def with_context(self, **context):
        """Temporarily set context for a series of log calls."""
        old_context = get_workflow_context()
        set_workflow_context(**context)
        return ContextualLogger(self.logger, old_context)

    def info(self, message, **extra):
        self.logger.info(message, extra=extra)

    def debug(self, message, **extra):
        self.logger.debug(message, extra=extra)

    def warning(self, message, **extra):
        self.logger.warning(message, extra=extra)

    def error(self, message, **extra):
        self.logger.error(message, extra=extra)

    def critical(self, message, **extra):
        self.logger.critical(message, extra=extra)


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

    def info(self, message, **extra):
        self.logger.info(message, extra=extra)

    def debug(self, message, **extra):
        self.logger.debug(message, extra=extra)

    def warning(self, message, **extra):
        self.logger.warning(message, extra=extra)

    def error(self, message, **extra):
        self.logger.error(message, extra=extra)


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
        console_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        root_logger.addHandler(console_handler)

    root_logger.setLevel(logging.INFO)


# Convenience function to get workflow-aware logger
def get_workflow_logger(name: str) -> WorkflowContextLogger:
    """Get a workflow-aware logger instance."""
    return WorkflowContextLogger(name)