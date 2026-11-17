"""The GPU of DocumentIA is asked for while the person fills in the form that
leads to a reading, not when the document arrives (starting it takes minutes)."""
import asyncio
import os
import sys

import networkx as nx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import documentia
from app.workflows import executor
from app.workflows.operators.document_interrogation import DocumentInterrogationOperator
from app.workflows.operators.user_input import UserInputOperator


def test_prepare_gpu_once_in_a_while_and_never_fails(monkeypatch):
    calls = []

    async def call(method, path, what, **kwargs):
        calls.append(path)
        if len(calls) == 2:
            raise documentia.DocumentIAError("down", retryable=True)
        return {"gpu": "starting"}

    monkeypatch.setattr(documentia, "configured", lambda: True)
    monkeypatch.setattr(documentia, "_call", call)
    documentia.reset_cache()
    asyncio.run(documentia.prepare_gpu())
    asyncio.run(documentia.prepare_gpu())              # asked a moment ago: nothing
    assert calls == ["/v1/gpu/preparar"]
    documentia._prepared["at"] = float("-inf")
    asyncio.run(documentia.prepare_gpu())              # fails quietly and may be asked again
    asyncio.run(documentia.prepare_gpu())
    assert calls == ["/v1/gpu/preparar"] * 3
    documentia.reset_cache()


class _Dag:
    def __init__(self, tasks, edges):
        self.tasks = tasks
        self.graph = nx.DiGraph(edges)


class _Instance:
    def __init__(self, dag):
        self.dag = dag


def test_only_a_form_that_leads_to_a_reading_asks_for_the_gpu():
    collect = UserInputOperator(task_id="collect", form_config={"fields": []})
    read = DocumentInterrogationOperator(task_id="read", document_type="identificacion",
                                         file_source="collect.ine")
    other = UserInputOperator(task_id="other", form_config={"fields": []})
    inst = _Instance(_Dag({"collect": collect, "read": read, "other": other},
                          [("collect", "read"), ("read", "other")]))
    waiting = {"waiting_for": "user_input"}
    assert executor._lectura_por_venir(inst, "collect", collect, waiting)
    assert executor._lectura_por_venir(inst, "read", read, waiting)          # asking for another document
    assert not executor._lectura_por_venir(inst, "read", read, {"waiting_for": "document_reading"})
    assert not executor._lectura_por_venir(inst, "other", other, waiting)
