"""
Dates in API responses.

Mongo hands back naive datetimes that are UTC. Serialized as-is ("2026-12-07T02:28:00")
browsers read them as local time and show them shifted by the UTC offset. New
endpoints pass their payload through `utc_aware` so every datetime carries
its offset ("...+00:00") and the browser converts it correctly.
"""
from datetime import datetime, timezone
from typing import Any


def utc_aware(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj if obj.tzinfo else obj.replace(tzinfo=timezone.utc)
    if isinstance(obj, dict):
        return {k: utc_aware(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [utc_aware(v) for v in obj]
    return obj
