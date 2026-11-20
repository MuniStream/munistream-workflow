"""
Submissions identified by the client (offline outbox of the citizen portal).

A capture made without signal is replayed when the device is back online. If
the original request did reach the server but the answer was lost, the replay
must not be processed again: the id the client generated is recorded here
(unique), and a second request with the same id is answered as already
received.
"""
from datetime import datetime

from beanie import Document, Indexed
from pydantic import Field


class EnvioCliente(Document):
    client_submission_id: Indexed(str, unique=True)
    instance_id: str
    task_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "client_submissions"
