"""
Operator scheduling strategy.

Each operator declares whether the executor should re-run it on a periodic
interval (POLLING) or only when explicitly resumed via resume_instance()
(EVENT_DRIVEN, default).

Most operators that leave an instance in PAUSED are waiting for a user action
that arrives through an API endpoint (e.g. /submit-workflow-data, /approve-step).
Those endpoints already call DAGExecutor.resume_instance(), so they need no
polling. A few operators wait on external systems without callbacks
(AirflowOperator, OpenProjectAssignmentOperator) and override the default
to declare an interval.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class OperatorPollingStrategy(str, Enum):
    EVENT_DRIVEN = "event_driven"
    POLLING = "polling"


@dataclass(frozen=True)
class PollingConfig:
    strategy: OperatorPollingStrategy = OperatorPollingStrategy.EVENT_DRIVEN
    polling_interval_seconds: Optional[int] = None

    @classmethod
    def event_driven(cls) -> "PollingConfig":
        return cls(strategy=OperatorPollingStrategy.EVENT_DRIVEN)

    @classmethod
    def polling(cls, interval_seconds: int) -> "PollingConfig":
        if interval_seconds is None or interval_seconds < 1:
            raise ValueError("polling interval must be >= 1 second")
        return cls(
            strategy=OperatorPollingStrategy.POLLING,
            polling_interval_seconds=interval_seconds,
        )
