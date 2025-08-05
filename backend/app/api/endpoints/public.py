"""
Public API endpoints that don't require authentication.
These endpoints are used by the citizen portal for browsing workflows.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query, Request, BackgroundTasks
from datetime import datetime

from ...workflows.registry import step_registry
from ...core.locale import get_locale_from_request
from ...core.i18n import t
from ...models.category import WorkflowCategory
from ...models.workflow import WorkflowDefinition

router = APIRouter()


@router.get("/workflows")
async def get_public_workflows(
    request: Request,
    category: Optional[str] = Query(None, description="Filter by category"),
    query: Optional[str] = Query(None, description="Search query"),
    sort_by: Optional[str] = Query("name", description="Sort by: name, duration, popularity"),
    sort_order: Optional[str] = Query("asc", description="Sort order: asc, desc")
):
    """Get all public workflows available for citizens to browse"""
    locale = get_locale_from_request(request)
    
    # Get workflows from database (WorkflowDefinition with assigned categories)
    from ...models.workflow import WorkflowDefinition
    
    # Build query filters
    query_filters = {}
    if category:
        query_filters["category"] = category
    
    # Get workflows from database
    db_workflows = await WorkflowDefinition.find(query_filters).to_list()
    
    # Convert to public format with additional metadata
    public_workflows = []
    
    for db_workflow in db_workflows:
        # Get workflow from registry for step details
        workflow = step_registry.get_workflow(db_workflow.workflow_id)
        if not workflow:
            continue
            
        # Get category name from database
        workflow_category_id = db_workflow.category or "general"
        workflow_category_name = "General"
        
        if db_workflow.category:
            # Look up category name from database
            category_doc = await WorkflowCategory.find_one({"category_id": db_workflow.category})
            if category_doc:
                workflow_category_name = category_doc.name
        
        # Calculate estimated duration based on step count
        step_count = len(workflow.steps)
        if step_count <= 5:
            estimated_duration = "1-3 days"
        elif step_count <= 10:
            estimated_duration = "3-7 days" 
        elif step_count <= 15:
            estimated_duration = "1-2 weeks"
        else:
            estimated_duration = "2-4 weeks"
        
        # Extract requirements from first few steps
        requirements = []
        for step in list(workflow.steps.values())[:3]:
            if hasattr(step, 'required_inputs') and step.required_inputs:
                for req_input in step.required_inputs:
                    if req_input not in requirements:
                        # Make requirements human-readable
                        req_readable = req_input.replace("_", " ").title()
                        requirements.append(req_readable)
        
        if not requirements:
            requirements = ["Valid identification", "Proof of address", "Required documents"]
        
        # Convert steps to public format
        public_steps = []
        for step in workflow.steps.values():
            public_steps.append({
                "id": step.step_id,
                "name": step.name,
                "description": step.description,
                "type": step.__class__.__name__.replace("Step", "").lower(),
                "estimatedDuration": "1-2 days" if "approval" in step.step_id else "Same day",
                "requirements": getattr(step, 'required_inputs', [])
            })
        
        # Translate workflow name and description
        workflow_name = workflow.name
        workflow_description = workflow.description
        
        # Check if we have translations for this workflow
        workflow_key = workflow.workflow_id.replace("_v1", "").replace("_", "_")
        translated_name = t(f"workflow.{workflow_key}", locale)
        translated_desc = t(f"workflow.{workflow_key}_description", locale)
        
        # Use translation if available, otherwise use original
        if translated_name != f"workflow.{workflow_key}":
            workflow_name = translated_name
        if translated_desc != f"workflow.{workflow_key}_description":
            workflow_description = translated_desc
        
        workflow_data = {
            "id": workflow.workflow_id,
            "name": workflow_name,
            "description": workflow_description,
            "category": workflow_category_name,
            "estimatedDuration": estimated_duration,
            "requirements": requirements[:5],  # Limit to 5 requirements
            "steps": public_steps,
            "isActive": True,
            "createdAt": datetime.now().isoformat(),
            "updatedAt": datetime.now().isoformat()
        }
        
        # Apply query filter (category filter is already applied at database level)
        if query and query.lower() not in workflow.name.lower() and query.lower() not in workflow.description.lower():
            continue
            
        public_workflows.append(workflow_data)
    
    # Sort workflows
    if sort_by == "name":
        public_workflows.sort(key=lambda x: x["name"], reverse=(sort_order == "desc"))
    elif sort_by == "duration":
        # Sort by estimated complexity (step count)
        public_workflows.sort(key=lambda x: len(x["steps"]), reverse=(sort_order == "desc"))
    elif sort_by == "popularity":
        # Sort by category preference (permits and licenses first)
        category_priority = {"Permits": 1, "Licenses": 2, "Registration": 3, "General": 4}
        public_workflows.sort(
            key=lambda x: category_priority.get(x["category"], 5), 
            reverse=(sort_order == "desc")
        )
    
    return public_workflows


@router.get("/workflows/featured")
async def get_featured_workflows(request: Request):
    """Get featured/popular workflows for the homepage"""
    
    # Get all workflows and select featured ones
    all_workflows = await get_public_workflows(request, None, None, None, None)
    
    # Prioritize permit and license workflows as featured
    featured = []
    for workflow in all_workflows:
        if workflow["category"] in ["Permits", "Licenses"]:
            featured.append(workflow)
    
    # Add registration if we have space
    for workflow in all_workflows:
        if workflow["category"] == "Registration" and len(featured) < 6:
            featured.append(workflow)
    
    # Limit to top 4 featured workflows
    return featured[:4]


@router.get("/workflows/{workflow_id}")
async def get_public_workflow_detail(workflow_id: str, request: Request):
    """Get detailed information about a specific workflow"""
    locale = get_locale_from_request(request)
    
    # Get workflow from registry
    workflow = step_registry.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail=t("workflow.not_found", locale))
    
    # Determine category
    category_map = {
        "citizen_registration": "Registration",
        "building_permit": "Permits", 
        "business_license": "Licenses",
        "complaint_handling": "Complaints",
        "permit_renewal": "Renewals"
    }
    
    workflow_category = "General"
    for key, cat in category_map.items():
        if key in workflow.workflow_id.lower():
            workflow_category = cat
            break
    
    # Calculate estimated duration
    step_count = len(workflow.steps)
    if step_count <= 5:
        estimated_duration = "1-3 days"
    elif step_count <= 10:
        estimated_duration = "3-7 days"
    elif step_count <= 15:
        estimated_duration = "1-2 weeks"
    else:
        estimated_duration = "2-4 weeks"
    
    # Extract comprehensive requirements
    requirements = []
    for step in workflow.steps.values():
        if hasattr(step, 'required_inputs') and step.required_inputs:
            for req_input in step.required_inputs:
                req_readable = req_input.replace("_", " ").title()
                if req_readable not in requirements:
                    requirements.append(req_readable)
    
    if not requirements:
        if "permit" in workflow_id.lower():
            requirements = [
                "Valid government-issued ID",
                "Proof of property ownership",
                "Construction plans and blueprints",
                "Property survey documents",
                "Insurance documentation"
            ]
        elif "license" in workflow_id.lower():
            requirements = [
                "Valid government-issued ID", 
                "Business registration documents",
                "Proof of business address",
                "Tax identification number",
                "Business plan documentation"
            ]
        else:
            requirements = [
                "Valid government-issued ID",
                "Proof of address",
                "Required application forms",
                "Supporting documentation"
            ]
    
    # Convert steps to detailed public format
    detailed_steps = []
    for i, step in enumerate(workflow.steps.values()):
        step_duration = "Same day"
        if "approval" in step.step_id:
            step_duration = "2-3 business days"
        elif "inspection" in step.step_id:
            step_duration = "3-5 business days"
        elif "payment" in step.step_id:
            step_duration = "Immediate"
        elif "verification" in step.step_id:
            step_duration = "1-2 business days"
        
        step_requirements = []
        if hasattr(step, 'required_inputs') and step.required_inputs:
            step_requirements = [req.replace("_", " ").title() for req in step.required_inputs]
        
        detailed_steps.append({
            "id": step.step_id,
            "name": step.name,
            "description": step.description,
            "type": step.__class__.__name__.replace("Step", "").lower(),
            "estimatedDuration": step_duration,
            "requirements": step_requirements
        })
    
    # Translate workflow name and description
    workflow_key = workflow.workflow_id.replace("_v1", "").replace("_", "_")
    translated_name = t(f"workflow.{workflow_key}", locale)
    translated_desc = t(f"workflow.{workflow_key}_description", locale)
    
    # Use translation if available, otherwise use original
    workflow_name = translated_name if translated_name != f"workflow.{workflow_key}" else workflow.name
    workflow_description = translated_desc if translated_desc != f"workflow.{workflow_key}_description" else workflow.description
    
    return {
        "id": workflow.workflow_id,
        "name": workflow_name,
        "description": workflow_description,
        "category": workflow_category,
        "estimatedDuration": estimated_duration,
        "requirements": requirements,
        "steps": detailed_steps,
        "isActive": True,
        "createdAt": datetime.now().isoformat(),
        "updatedAt": datetime.now().isoformat()
    }




@router.post("/workflows/{workflow_id}/start")
async def start_workflow_instance(
    workflow_id: str,
    request: Request,
    background_tasks: BackgroundTasks
):
    """Start a new workflow instance for a citizen (public endpoint)"""
    locale = get_locale_from_request(request)
    
    # Get request body as JSON
    try:
        body = await request.json()
        initial_data = body if isinstance(body, dict) else {}
    except:
        initial_data = {}
    
    # Validate workflow exists
    workflow = step_registry.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(
            status_code=404, 
            detail=t("workflow.not_found", locale)
        )
    
    # Import required services
    from ...services.workflow_service import InstanceService
    import uuid
    
    # Create anonymous user ID for tracking (in real system, use actual user auth)
    citizen_id = f"citizen_{uuid.uuid4().hex[:8]}"
    
    # Create workflow instance
    try:
        instance = await InstanceService.create_instance(
            workflow_id=workflow_id,
            user_id=citizen_id,
            initial_context=initial_data,
            user_data={
                "type": "citizen",
                "started_from": "public_portal",
                "locale": locale
            }
        )
        
        # Execute workflow in background to handle citizen input steps
        from .instances import execute_workflow_instance
        background_tasks.add_task(execute_workflow_instance, instance.instance_id)
        
        # Return instance details for citizen tracking
        return {
            "instance_id": instance.instance_id,
            "workflow_id": workflow_id,
            "workflow_name": workflow.name,
            "citizen_tracking_id": citizen_id,
            "status": instance.status,
            "created_at": instance.created_at,
            "next_step": instance.current_step,
            "tracking_url": f"/track/{instance.instance_id}",
            "message": t("workflow.started_successfully", locale)
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=t("workflow.start_failed", locale, error=str(e))
        )


@router.get("/track/{instance_id}")
async def track_workflow_instance(instance_id: str, request: Request):
    """Track workflow instance progress (public endpoint)"""
    locale = get_locale_from_request(request)
    
    # Import required models
    from ...models.workflow import WorkflowInstance, StepExecution, WorkflowStep
    
    # Get instance
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(
            status_code=404,
            detail=t("instance.not_found", locale)
        )
    
    # Get workflow from registry for metadata
    workflow = step_registry.get_workflow(instance.workflow_id)
    if not workflow:
        raise HTTPException(
            status_code=404,
            detail=t("workflow.not_found", locale)
        )
    
    # Get total steps for progress calculation
    total_steps = await WorkflowStep.find(WorkflowStep.workflow_id == instance.workflow_id).count()
    
    # Calculate progress
    completed_count = len(instance.completed_steps)
    progress_percentage = (completed_count / total_steps * 100) if total_steps > 0 else 0
    
    # Get step executions for detailed tracking
    step_executions = await StepExecution.find(
        StepExecution.instance_id == instance_id
    ).sort(StepExecution.started_at).to_list()
    
    # Translate workflow name
    workflow_key = workflow.workflow_id.replace("_v1", "").replace("_", "_")
    translated_name = t(f"workflow.{workflow_key}", locale)
    workflow_name = translated_name if translated_name != f"workflow.{workflow_key}" else workflow.name
    
    # Build step progress
    step_progress = []
    current_step_data = None
    
    for step_id, step in workflow.steps.items():
        step_status = "pending"
        step_started = None
        step_completed = None
        requires_citizen_input = False
        input_form = {}
        
        # Check if this step requires citizen input (for all steps, not just current)
        if hasattr(step, 'requires_citizen_input'):
            requires_citizen_input = step.requires_citizen_input
            if requires_citizen_input and hasattr(step, 'input_form'):
                input_form = step.input_form
        
        if step_id in instance.completed_steps:
            step_status = "completed"
        elif step_id in instance.failed_steps:
            step_status = "failed"
        elif step_id == instance.current_step:
            step_status = "in_progress"
        
        # Find execution details
        execution = next((ex for ex in step_executions if ex.step_id == step_id), None)
        if execution:
            step_started = execution.started_at
            step_completed = execution.completed_at
        
        step_data = {
            "step_id": step_id,
            "name": step.name,
            "description": step.description,
            "status": step_status,
            "started_at": step_started,
            "completed_at": step_completed,
            "requires_citizen_input": requires_citizen_input,
            "input_form": input_form
        }
        
        step_progress.append(step_data)
        
        # Set current step data for easy access
        if step_status == "in_progress":
            current_step_data = step_data
    
    # Check if citizen input is required right now
    # This happens when:
    # 1. Instance status is "awaiting_input", OR
    # 2. Current step requires citizen input
    requires_input = (
        instance.status == "awaiting_input" or 
        (current_step_data and current_step_data.get("requires_citizen_input", False))
    )
    
    # If awaiting input, find the step that needs input
    input_step_data = None
    if instance.status == "awaiting_input":
        # Look for the current step or first step that requires input
        target_step_id = instance.current_step
        if not target_step_id and workflow.start_step:
            target_step_id = workflow.start_step.step_id
            
        for step_data in step_progress:
            if step_data["step_id"] == target_step_id and step_data.get("requires_citizen_input", False):
                input_step_data = step_data
                break
    elif current_step_data and current_step_data.get("requires_citizen_input", False):
        input_step_data = current_step_data
    
    return {
        "instance_id": instance_id,
        "workflow_id": instance.workflow_id,
        "workflow_name": workflow_name,
        "status": instance.status,
        "progress_percentage": round(progress_percentage, 2),
        "current_step": instance.current_step,
        "created_at": instance.created_at,
        "updated_at": instance.updated_at,
        "completed_at": instance.completed_at,
        "total_steps": total_steps,
        "completed_steps": completed_count,
        "step_progress": step_progress,
        "requires_input": requires_input,
        "input_form": input_step_data.get("input_form", {}) if input_step_data else {},
        "estimated_completion": None,  # TODO: Add estimation logic
        "message": t("instance.tracking_info", locale)
    }


@router.post("/instances/{instance_id}/submit-data")
async def submit_citizen_data(
    instance_id: str,
    request: Request,
    background_tasks: BackgroundTasks
):
    """Submit citizen data for a workflow step that requires input"""
    locale = get_locale_from_request(request)
    
    # Import required models
    from ...models.workflow import WorkflowInstance, StepExecution
    from uuid import uuid4
    from datetime import datetime
    
    # Find the workflow instance
    instance = await WorkflowInstance.find_one(WorkflowInstance.instance_id == instance_id)
    if not instance:
        raise HTTPException(
            status_code=404,
            detail=t("instance.not_found", locale)
        )
    
    # Check if instance is in a state that can accept input
    if instance.status not in ["running", "paused", "awaiting_input"]:
        raise HTTPException(
            status_code=400, 
            detail="Instance is not in a state to accept input"
        )
    
    # Get workflow from registry to check current step
    workflow = step_registry.get_workflow(instance.workflow_id)
    if not workflow:
        raise HTTPException(
            status_code=404,
            detail=t("workflow.not_found", locale)
        )
    
    # Find the step that requires input
    input_step_id = None
    input_step = None
    
    if instance.status == "awaiting_input":
        # For awaiting_input instances, check current step or start step
        input_step_id = instance.current_step or (workflow.start_step.step_id if workflow.start_step else None)
    else:
        # For running instances, use current step
        input_step_id = instance.current_step
    
    if not input_step_id or input_step_id not in workflow.steps:
        raise HTTPException(
            status_code=400,
            detail="No valid step found that requires input"
        )
    
    input_step = workflow.steps[input_step_id]
    if not hasattr(input_step, 'requires_citizen_input') or not input_step.requires_citizen_input:
        raise HTTPException(
            status_code=400,
            detail="Step does not require citizen input"
        )
    
    try:
        # Get form data and files from request
        form = await request.form()
        citizen_data = {}
        uploaded_files = {}
        
        for key, value in form.items():
            if hasattr(value, 'filename'):  # It's a file
                # In a real implementation, you'd save this to cloud storage
                file_content = await value.read()
                uploaded_files[key] = {
                    "filename": value.filename,
                    "content_type": value.content_type,
                    "size": len(file_content)
                }
                # Reset file pointer for potential future reads
                await value.seek(0)
            else:
                citizen_data[key] = value
        
        # Update the instance with submitted data
        instance.context = instance.context or {}
        instance.context[f"{input_step_id}_citizen_data"] = citizen_data
        instance.context[f"{input_step_id}_uploaded_files"] = uploaded_files
        instance.context[f"{input_step_id}_data_submitted_at"] = datetime.now().isoformat()
        
        # Mark step as completed and resume workflow
        if input_step_id not in instance.completed_steps:
            instance.completed_steps.append(input_step_id)
        
        # Update instance status to pending validation after citizen data submission
        if instance.status == "awaiting_input":
            instance.status = "pending_validation"
            if not instance.current_step:
                instance.current_step = input_step_id
        
        # Don't move to next step automatically - wait for admin validation
        # The workflow will resume after admin approves/rejects the data
            
        instance.updated_at = datetime.utcnow()
        await instance.save()
        
        # Create step execution record for the completed citizen input step
        step_execution = StepExecution(
            execution_id=str(uuid4()),
            instance_id=instance.instance_id,
            step_id=input_step_id,
            workflow_id=instance.workflow_id,
            status="completed",
            inputs=citizen_data,
            outputs={"citizen_data_submitted": True, "files_uploaded": len(uploaded_files)},
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
            duration_seconds=0
        )
        await step_execution.create()
        
        # No automatic workflow execution - wait for admin validation
        
        return {
            "success": True,
            "message": "Data submitted successfully",
            "next_action": "Your data has been submitted and is being processed. Please check back for updates.",
            "locale": locale
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to submit data: {str(e)}"
        )


@router.get("/workflow-categories")
async def get_public_workflow_categories(request: Request):
    """Get workflow categories for public browsing"""
    locale = get_locale_from_request(request)
    
    # Get categories from database
    categories = await WorkflowCategory.find(
        {"is_active": True}
    ).sort([("display_order", 1), ("name", 1)]).to_list()
    
    # Convert to public format with workflow counts
    public_categories = []
    
    for category in categories:
        # Get workflow count for this category - only count workflows that are available in registry
        db_workflows = await WorkflowDefinition.find({"category": category.category_id}).to_list()
        
        # Count only workflows that exist in the step registry
        workflow_count = 0
        for db_workflow in db_workflows:
            workflow = step_registry.get_workflow(db_workflow.workflow_id)
            if workflow:
                workflow_count += 1
                
        if workflow_count > 0:  # Only include categories with workflows
            public_categories.append({
                "id": category.category_id,
                "name": category.name,
                "description": category.description,
                "icon": category.icon,
                "color": category.color,
                "category_type": category.category_type,
                "is_featured": category.is_featured,
                "workflowCount": workflow_count
            })
    
    return public_categories