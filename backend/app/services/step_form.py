"""
The form of a waiting step, as the track endpoints return it.

An operator that waits for input returns its form in `form_config`; the
executor merges that output into the instance context, so `context
["form_config"]` holds the form of whichever step waited last. Two problems
followed from reading it back:

- The track endpoints merged the step's static `form_config` on top of the
  dynamic one, so anything an operator computed at run time (an amount to pay,
  a rejection reason) was overwritten by the static key of the same name.
  Tenant operators had to leave those keys out of their static form on purpose.
- Nothing said which step the context form belonged to, so a step that waits
  without a form (a child workflow, an integration) could show the previous
  step's form.

The executor now records the owner of the context form (`FORM_OWNER_KEY`).
The form of a step is its static form with the dynamic one on top, and the
context form only counts when it belongs to that step. Instances paused before
the owner was recorded keep the previous merge order.

Operators that reject a submission (user input, catalog selection, entity
count, live capture) return their errors next to the form, not inside it, so
neither the portal nor an agent saw why the step was asked again. The errors
of the step's last attempt are copied into the form as `validation_errors`.
"""
from typing import Any, Dict, Optional

FORM_OWNER_KEY = "form_config_task_id"
_UNRECORDED = object()


def record_form_owner(context: Dict[str, Any], task_id: str, output: Optional[Dict[str, Any]]) -> None:
    """Called by the executor when `task_id` starts waiting with `output`."""
    context[FORM_OWNER_KEY] = task_id if (output and output.get("form_config")) else None


def context_form_belongs_to(context: Dict[str, Any], task_id: str) -> bool:
    owner = context.get(FORM_OWNER_KEY, _UNRECORDED)
    return owner is _UNRECORDED or owner == task_id


def waiting_step_form(context: Dict[str, Any], task_id: str, task: Any,
                      task_state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    form = _step_form(context, task_id, task, task_state)
    errors = _attempt_errors((task_state or {}).get("output_data") or {})
    if errors and not form.get("validation_errors"):
        form["validation_errors"] = errors
    return form


def _attempt_errors(output: Dict[str, Any]) -> Any:
    errors = output.get("validation_errors")
    if errors:
        return errors
    single = output.get("validation_error")
    return [single] if single else None


def _step_form(context: Dict[str, Any], task_id: str, task: Any,
               task_state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    static = getattr(task, "form_config", None) if task is not None else None
    static = static if isinstance(static, dict) else {}
    output_form = ((task_state or {}).get("output_data") or {}).get("form_config")
    owner = context.get(FORM_OWNER_KEY, _UNRECORDED)

    if output_form:
        return {**static, **output_form}
    dynamic = context.get("form_config") if context_form_belongs_to(context, task_id) else None
    if not isinstance(dynamic, dict):
        return dict(static)
    if owner is _UNRECORDED:
        return {**dynamic, **static}
    return {**static, **dynamic}
