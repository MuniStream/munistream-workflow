"""Citizens never see or start administrative workflows through /public."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.workflow import WorkflowType
from app.services import citizen_workflows
from app.services.citizen_workflows import citizen_workflow_or_404, is_citizen_workflow


def test_only_process_and_document_workflows_are_for_citizens():
    assert is_citizen_workflow(SimpleNamespace(workflow_type=WorkflowType.PROCESS))
    assert is_citizen_workflow(SimpleNamespace(workflow_type=WorkflowType.DOCUMENT_PROCESSING))
    assert is_citizen_workflow(SimpleNamespace(workflow_type=None))      # the model's default is process
    assert not is_citizen_workflow(SimpleNamespace(workflow_type=WorkflowType.ADMIN))
    assert not is_citizen_workflow(SimpleNamespace(workflow_type="admin"))


@pytest.mark.asyncio
async def test_an_admin_workflow_answers_like_a_missing_one(monkeypatch):
    definitions = {"opinion_tecnica": SimpleNamespace(workflow_type=WorkflowType.ADMIN),
                   "pesca": SimpleNamespace(workflow_type=WorkflowType.PROCESS)}

    async def get_workflow_definition(workflow_id):
        return definitions.get(workflow_id)
    from app.services import workflow_service as ws
    monkeypatch.setattr(ws.workflow_service, "get_workflow_definition", get_workflow_definition)
    assert await citizen_workflow_or_404("pesca") is definitions["pesca"]
    for workflow_id in ("opinion_tecnica", "no_existe"):
        with pytest.raises(HTTPException) as error:
            await citizen_workflow_or_404(workflow_id)
        assert error.value.status_code == 404
