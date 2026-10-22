"""
The workflow logger must never raise, and must keep the error it is reporting.

`WorkflowContextLogger` forwarded every keyword argument as `extra=`, so the
usual `logger.error("...", exc_info=True)` inside an `except` block became
`extra={"exc_info": True}`. The standard library refuses to overwrite a
LogRecord attribute and raises `KeyError: "Attempt to overwrite 'exc_info' in
LogRecord"` from inside the handler. That replaced the original exception:

- In production, a `WorkflowStartOperator` whose child workflow was not
  registered failed with that KeyError instead of "Workflow ... not found",
  which hid the real cause.
- The assignment endpoints that log with `exc_info=True` answered 500 with the
  same KeyError.

Logging keywords (`exc_info`, `stack_info`, `stacklevel`) must reach the
logger as such, and context fields that collide with LogRecord attributes must
be kept under another name instead of raising.
"""

import logging

import pytest

from app.core.logging_config import ContextualLogger, get_workflow_logger


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def capture():
    handler = _Capture()
    target = logging.getLogger("tests.workflow_logger")
    target.addHandler(handler)
    target.setLevel(logging.DEBUG)
    yield handler
    target.removeHandler(handler)


def _raise_and_log(log):
    try:
        raise ValueError("Workflow opinion_tecnica_imipas not found")
    except ValueError:
        log.error("Exception in _create_child_workflow", exc_info=True)


def test_exc_info_is_passed_to_the_logger_not_to_extra(capture):
    _raise_and_log(get_workflow_logger("tests.workflow_logger"))

    [record] = capture.records
    assert record.getMessage() == "Exception in _create_child_workflow"
    assert record.exc_info is not None
    assert record.exc_info[0] is ValueError
    assert "opinion_tecnica_imipas not found" in str(record.exc_info[1])
    # The record points at the caller, not at the wrapper.
    assert record.funcName == "_raise_and_log"


def test_context_fields_still_go_to_extra(capture):
    log = get_workflow_logger("tests.workflow_logger")
    log.error("Failed to list assignments", error="boom", exc_info=False)

    [record] = capture.records
    assert record.error == "boom"
    assert not record.exc_info


@pytest.mark.parametrize("level", ["debug", "info", "warning", "error", "critical"])
def test_every_level_accepts_logging_keywords(capture, level):
    log = get_workflow_logger("tests.workflow_logger")
    getattr(log, level)("message", stack_info=True)

    [record] = capture.records
    assert record.stack_info


def test_reserved_names_in_context_do_not_raise(capture):
    log = get_workflow_logger("tests.workflow_logger")
    log.info("text", message="other", name="field", module="m")

    [record] = capture.records
    assert record.getMessage() == "text"
    assert record.name == "tests.workflow_logger"
    assert record.extra_message == "other"
    assert record.extra_name == "field"
    assert record.extra_module == "m"


def test_exception_logs_the_active_exception(capture):
    log = get_workflow_logger("tests.workflow_logger")
    try:
        raise RuntimeError("child failed")
    except RuntimeError:
        log.exception("Child workflow failed")

    [record] = capture.records
    assert record.levelno == logging.ERROR
    assert record.exc_info[0] is RuntimeError


def test_contextual_logger_accepts_logging_keywords(capture):
    log = ContextualLogger(logging.getLogger("tests.workflow_logger"), {})
    _raise_and_log(log)

    [record] = capture.records
    assert record.exc_info[0] is ValueError
