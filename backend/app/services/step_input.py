"""
What `submit-data` stores as the input of the waiting step.

The backend saves the submitted body under `context["<task>_input"]`, which is
where every operator reads it. Some clients wrap the body in that same key
(`{"<task>_input": {...}}`): the admin portal does it for assertion review and
catalog selection, the citizen portal for assertion review. Stored as is, the
operator found `{"<task>_input": {...}}` and read its fields as missing, so an
assertion review confirmed every assertion and ignored the reviewer's overrides,
and a catalog selection arrived empty.

A body made of exactly that one key is unwrapped; anything else is stored
unchanged (including `{"<task>_selections": ...}`, which the entity picker
reads under that name).
"""
from typing import Any


def normalize_step_input(task_id: str, data: Any) -> Any:
    key = f"{task_id}_input"
    if isinstance(data, dict) and len(data) == 1 and key in data:
        return data[key]
    return data
