"""
Simple, clean data submission endpoint for workflows.
"""
from fastapi import APIRouter, HTTPException, Request
from typing import Dict, Any
from datetime import datetime

from ...services.workflow_service import workflow_service
from ...models.workflow import WorkflowInstance

router = APIRouter()


@router.post("/instances/{instance_id}/submit-data")
async def submit_data(
    instance_id: str,
    request: Request
):
    """
    Submit data for a workflow instance waiting for input.
    Simple and foolproof.
    """
    # Get the database instance
    db_instance = await WorkflowInstance.find_one(
        WorkflowInstance.instance_id == instance_id
    )
    if not db_instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    # Get the DAG instance
    dag_instance = await workflow_service.get_instance(instance_id)
    if not dag_instance:
        raise HTTPException(status_code=404, detail="Instance not found in DAG")
    
    # Find which task is waiting for input
    waiting_task = None
    for task_id, state in dag_instance.task_states.items():
        if state.get("status") == "waiting":
            waiting_task = task_id
            break
    
    if not waiting_task:
        raise HTTPException(
            status_code=400, 
            detail="Instance is not waiting for input"
        )
    
    # Get the submitted data
    data = await request.json()
    
    # Update the DAG instance context with the input
    dag_instance.context[f"{waiting_task}_input"] = data
    dag_instance.context[f"{waiting_task}_submitted_at"] = datetime.utcnow().isoformat()
    
    # Update the database instance
    db_instance.context = dag_instance.context
    db_instance.updated_at = datetime.utcnow()
    await db_instance.save()
    
    # Resume execution
    workflow_service.executor.resume_instance(instance_id)
    
    return {
        "success": True,
        "message": "Data submitted successfully",
        "instance_id": instance_id,
        "task": waiting_task
    }