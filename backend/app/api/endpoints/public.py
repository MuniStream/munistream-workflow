"""
Public API endpoints for workflows.
Simplified and focused on clarity.
"""
from fastapi import APIRouter, HTTPException, Request, BackgroundTasks, Query, Depends, Header
from typing import Dict, Any, List, Optional
from datetime import datetime
import json
import base64
import logging
from pydantic import BaseModel

from ...services.workflow_service import workflow_service
from ...models.workflow import WorkflowInstance, WorkflowDefinition
from ...models.customer import Customer
from .public_auth import get_current_customer_optional
from ...models.legal_entity import LegalEntity, EntityType
from ...services.entity_service import EntityService
from ...workflows.dag import DAG
from .public_auth import router as auth_router, get_current_customer
from ...core.logging_config import set_workflow_context
# Removed localization imports - keeping it simple

router = APIRouter()

# Include auth endpoints under /public
router.include_router(auth_router)


@router.post("/instances/{instance_id}/submit-data")
async def submit_data(
    instance_id: str, 
    request: Request,
    current_customer: Customer = Depends(get_current_customer)
):
    """
    Submit data for a workflow waiting for input.
    Simple: Get data, save to context, resume execution.
    Requires authentication.
    """
    # Get database instance
    db_instance = await WorkflowInstance.find_one(
        WorkflowInstance.instance_id == instance_id
    )
    if not db_instance:
        raise HTTPException(status_code=404, detail="Instance not found")
    
    # Verify the instance belongs to the current customer
    if db_instance.user_id != str(current_customer.id):
        raise HTTPException(status_code=403, detail="Not authorized to access this instance")
    
    # Get DAG instance
    dag_instance = await workflow_service.get_instance(instance_id)
    if not dag_instance:
        raise HTTPException(status_code=404, detail="Instance not found in workflow system")
    
    # Find waiting task
    waiting_task = None
    for task_id, state in dag_instance.task_states.items():
        if state.get("status") == "waiting":
            waiting_task = task_id
            break
    
    if not waiting_task:
        raise HTTPException(status_code=400, detail="Instance is not waiting for input")
    
    # Get submitted data - handle both JSON and FormData
    content_type = request.headers.get("content-type", "")
    
    if "multipart/form-data" in content_type:
        # Handle FormData
        form = await request.form()
        data = {}
        for key, value in form.items():
            # Handle file uploads separately if needed
            if hasattr(value, 'filename'):
                # This is a file upload - read the file content
                import base64
                file_content = await value.read()
                # Convert to base64 for storage
                file_base64 = base64.b64encode(file_content).decode('utf-8')
                
                data[key] = {
                    "filename": value.filename,
                    "content_type": value.content_type,
                    "base64": file_base64,  # Store the actual file content as base64
                    "size": len(file_content)
                }
            else:
                data[key] = value
    else:
        # Handle JSON
        data = await request.json()
    
    # Update BOTH the DAG instance and database with input
    dag_instance.context[f"{waiting_task}_input"] = data
    dag_instance.context[f"{waiting_task}_submitted_at"] = datetime.utcnow().isoformat()

    # Change status from paused to running to avoid delays
    if db_instance.status == "paused":
        db_instance.status = "running"

    # Save to database
    db_instance.context = dag_instance.context
    db_instance.updated_at = datetime.utcnow()
    await db_instance.save()

    # Resume execution - the DAG instance now has the data in context
    workflow_service.executor.resume_instance(instance_id)
    
    return {
        "success": True,
        "message": "Data submitted successfully",
        "instance_id": instance_id
    }




@router.post("/start-workflow")
async def start_workflow(request: Dict[str, Any]):
    """
    DEPRECATED: This endpoint is disabled for security.
    Use authenticated endpoints instead.
    """
    raise HTTPException(
        status_code=403, 
        detail="Public workflow creation is disabled. Please use authenticated endpoints."
    )


@router.get("/workflows")
async def list_public_workflows(
    workflow_type: str = Query("process", description="Filter by workflow type (process, document_processing, admin, etc.)")
):
    """
    List available workflows for public use.
    Defaults to showing only PROCESS type workflows (user-facing processes).
    No authentication required - citizens can browse available services.
    """
    workflows = await workflow_service.list_workflow_definitions(
        status="active",
        workflow_type=workflow_type,
        limit=100
    )

    result = []
    for w in workflows:
        dag = workflow_service.dag_bag.get_dag(w.workflow_id)
        workflow_data = await _get_workflow_data(w, dag, "es")
        result.append(workflow_data)

    # Sort workflows alphabetically by name
    result.sort(key=lambda x: x.get('name', '').lower())

    return {"workflows": result}


@router.get("/workflows/documents")
async def list_document_processing_workflows(
    locale: str = Query("es", description="Language locale (es/en)")
):
    """
    List available document processing workflows.
    These are workflows that citizens use to upload and process documents.
    No authentication required - citizens can browse available document services.
    """
    workflows = await workflow_service.list_workflow_definitions(
        status="active",
        workflow_type="document_processing",
        limit=100
    )

    result = []
    for w in workflows:
        dag = workflow_service.dag_bag.get_dag(w.workflow_id)
        workflow_data = await _get_workflow_data(w, dag, locale)
        result.append(workflow_data)

    # Sort document workflows alphabetically by name
    result.sort(key=lambda x: x.get('name', '').lower())

    return {"documents": result}


@router.get("/workflows/featured")
async def get_featured_workflows(
    locale: str = Query("es", description="Language locale (es/en)")
):
    """
    Get featured workflows for the citizen portal homepage.
    No authentication required - public can see featured services.
    Returns workflows marked as featured or popular.
    """
    # Get all active workflows
    all_workflows = await workflow_service.list_workflow_definitions(status="active")
    
    # For now, return first 6 workflows as featured
    # In production, you'd filter by a "featured" flag or popularity metrics
    featured = all_workflows[:6] if all_workflows else []
    
    # Prepare response with real workflow data
    result = []
    for w in featured:
        dag = workflow_service.dag_bag.get_dag(w.workflow_id)
        workflow_data = await _get_workflow_data(w, dag, locale)
        # Add compatibility field
        workflow_data["estimatedTime"] = workflow_data["estimatedDuration"]
        result.append(workflow_data)

    # Sort featured workflows alphabetically by name
    result.sort(key=lambda x: x.get('name', '').lower())
    
    return {
        "featured": result,
        "locale": locale
    }


@router.get("/workflows/categories")
async def get_workflow_categories(
    locale: str = Query("es", description="Language locale (es/en)")
):
    """
    Get workflow categories for filtering.
    No authentication required - public can browse categories.
    """
    # Define available categories with translations
    categories = {
        "es": [
            {"id": "automated", "name": "Procesos Automatizados", "icon": "smart_toy", "count": 0},
            {"id": "permits", "name": "Permisos y Licencias", "icon": "description", "count": 0},
            {"id": "property", "name": "Propiedad y Catastro", "icon": "home", "count": 0},
            {"id": "business", "name": "Negocios", "icon": "business", "count": 0},
            {"id": "construction", "name": "Construcción", "icon": "construction", "count": 0},
            {"id": "environment", "name": "Medio Ambiente", "icon": "nature", "count": 0},
            {"id": "social", "name": "Servicios Sociales", "icon": "people", "count": 0},
            {"id": "general", "name": "General", "icon": "assignment", "count": 0},
        ],
        "en": [
            {"id": "automated", "name": "Automated Processes", "icon": "smart_toy", "count": 0},
            {"id": "permits", "name": "Permits & Licenses", "icon": "description", "count": 0},
            {"id": "property", "name": "Property & Registry", "icon": "home", "count": 0},
            {"id": "business", "name": "Business", "icon": "business", "count": 0},
            {"id": "construction", "name": "Construction", "icon": "construction", "count": 0},
            {"id": "environment", "name": "Environment", "icon": "nature", "count": 0},
            {"id": "social", "name": "Social Services", "icon": "people", "count": 0},
            {"id": "general", "name": "General", "icon": "assignment", "count": 0},
        ]
    }
    
    # Count workflows per category - use DAG data for accurate counts
    all_workflows = await workflow_service.list_workflow_definitions(status="active")
    category_counts = {}
    for w in all_workflows:
        # Get category from DAG if available (more accurate than database)
        dag = workflow_service.dag_bag.get_dag(w.workflow_id)
        if dag and hasattr(dag, 'category'):
            cat = dag.category
        else:
            cat = w.category or "general"
        category_counts[cat] = category_counts.get(cat, 0) + 1
    
    # Update counts
    selected_categories = categories.get(locale, categories["es"])
    for cat in selected_categories:
        cat["count"] = category_counts.get(cat["id"], 0)
    
    return {
        "categories": selected_categories,
        "total": len(all_workflows)
    }


@router.get("/workflows/search")
async def search_workflows(
    q: str = Query("", description="Search query"),
    category: Optional[str] = Query(None, description="Filter by category"),
    locale: str = Query("es", description="Language locale")
):
    """
    Search workflows by name or description.
    """
    all_workflows = await workflow_service.list_workflow_definitions(status="active")
    
    # Build a list with DAG data for accurate searching
    workflows_with_dag_data = []
    for w in all_workflows:
        dag = workflow_service.dag_bag.get_dag(w.workflow_id)
        if dag:
            # Use DAG data if available
            name = dag.name if hasattr(dag, 'name') else w.name
            description = dag.description if dag.description else w.description
            cat = dag.category if hasattr(dag, 'category') else (w.category or "general")
        else:
            name = w.name
            description = w.description
            cat = w.category or "general"
        
        workflows_with_dag_data.append({
            "workflow": w,
            "dag": dag,
            "name": name,
            "description": description,
            "category": cat
        })
    
    # Filter by search query
    if q:
        q_lower = q.lower()
        filtered = [
            wd for wd in workflows_with_dag_data
            if q_lower in wd["name"].lower() or 
               (wd["description"] and q_lower in wd["description"].lower())
        ]
    else:
        filtered = workflows_with_dag_data
    
    # Filter by category
    if category:
        filtered = [wd for wd in filtered if wd["category"] == category]
    
    # Build results with real workflow data
    results = []
    for wd in filtered:
        workflow_data = await _get_workflow_data(wd["workflow"], wd["dag"], locale)
        results.append(workflow_data)

    # Sort search results alphabetically by name
    results.sort(key=lambda x: x.get('name', '').lower())
    
    return {
        "results": results,
        "total": len(filtered),
        "query": q,
        "category": category
    }


@router.get("/workflows/{workflow_id}")
async def get_workflow_by_id(
    workflow_id: str,
    locale: str = Query("es", description="Language locale (es/en)")
):
    """
    Get a specific workflow by ID with all its details.
    No authentication required - public can view workflow details.
    """
    # Get workflow definition from database
    workflow = await workflow_service.get_workflow_definition(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Get the actual DAG to get real data
    dag = workflow_service.dag_bag.get_dag(workflow_id)
    workflow_data = await _get_workflow_data(workflow, dag, locale)
    
    # Add additional details for single workflow view
    if workflow.created_at:
        workflow_data["created_at"] = workflow.created_at.isoformat()
    if workflow.updated_at:
        workflow_data["updated_at"] = workflow.updated_at.isoformat()
    
    workflow_data["metadata"] = workflow.metadata or {}
    
    return workflow_data


@router.get("/workflows/{workflow_id}/pre-check")
async def pre_check_workflow(
    workflow_id: str,
    current_customer: Optional[Customer] = Depends(get_current_customer_optional)
):
    """
    Pre-check all operator requirements for a workflow.
    Returns information about what the workflow needs before it can be started.

    This endpoint checks all operators in the workflow and returns:
    - Overall readiness status
    - Requirements for each operator
    - What resources are available
    - What actions are needed to fulfill missing requirements
    """
    # Get workflow definition
    workflow = await workflow_service.get_workflow_definition(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Get the actual DAG
    dag = workflow_service.dag_bag.get_dag(workflow_id)
    if not dag:
        raise HTTPException(status_code=404, detail="Workflow DAG not found")

    # Build context for pre-check
    context = {
        "workflow_id": workflow_id,
        "user_id": str(current_customer.id) if current_customer else None
    }

    # Pre-check each operator in the DAG
    operator_checks = []
    overall_ready = True

    for task_id, task in dag.tasks.items():
        # Get the operator
        operator = task

        # Run pre-check for this operator
        try:
            check_result = await operator.pre_check(context)

            operator_info = {
                "task_id": task_id,
                "task_type": operator.__class__.__name__,
                "ready": check_result.get("ready", True),
                "requirements": check_result.get("requirements", []),
                "message": check_result.get("message", ""),
                "missing_critical": check_result.get("missing_critical", []),
                "missing_optional": check_result.get("missing_optional", [])
            }

            # Update overall readiness
            if not check_result.get("ready", True):
                overall_ready = False

            operator_checks.append(operator_info)

        except Exception as e:
            # If pre-check fails, mark as not ready
            operator_checks.append({
                "task_id": task_id,
                "task_type": operator.__class__.__name__,
                "ready": False,
                "requirements": [],
                "message": f"Error checking requirements: {str(e)}",
                "missing_critical": [],
                "missing_optional": []
            })
            overall_ready = False

    # Build response
    response = {
        "workflow_id": workflow_id,
        "workflow_name": workflow.name,
        "overall_ready": overall_ready,
        "operator_checks": operator_checks,
        "summary": {
            "total_operators": len(operator_checks),
            "ready_operators": sum(1 for op in operator_checks if op["ready"]),
            "blocked_operators": sum(1 for op in operator_checks if not op["ready"])
        }
    }

    # Add user-friendly message
    if overall_ready:
        response["message"] = "All requirements fulfilled. Workflow is ready to start."
    else:
        blocked_count = response["summary"]["blocked_operators"]
        response["message"] = f"{blocked_count} operator(s) have missing requirements. Please review and fulfill them before starting."

    return response


def _get_workflow_icon(category: Optional[str]) -> str:
    """Get icon name based on workflow category."""
    icons = {
        "permits": "description",
        "property": "home",
        "business": "business",
        "construction": "construction",
        "environment": "nature",
        "social": "people",
        "tax": "account_balance",
        "health": "local_hospital",
        "automated": "smart_toy"
    }
    return icons.get(category or "general", "assignment")


def _get_workflow_requirements(workflow: WorkflowDefinition, locale: str) -> List[str]:
    """Get workflow requirements from metadata or defaults."""
    # Try to get from workflow metadata first
    if workflow.metadata and "requirements" in workflow.metadata:
        reqs = workflow.metadata["requirements"]
        if isinstance(reqs, dict) and locale in reqs:
            return reqs[locale]
        elif isinstance(reqs, list):
            return reqs
    
    # Return empty list if no requirements defined
    return []


def _calculate_duration(num_steps: int, avg_seconds_per_step: int = 180) -> str:
    """Calculate estimated duration based on steps and metadata."""
    total_seconds = num_steps * avg_seconds_per_step
    minutes = total_seconds // 60
    
    if minutes <= 10:
        return "5-10 min"
    elif minutes <= 20:
        return "10-20 min" 
    elif minutes <= 30:
        return "20-30 min"
    elif minutes <= 45:
        return "30-45 min"
    else:
        return f"{minutes}-{minutes+15} min"


@router.get("/entities")
async def list_user_entities(
    current_customer: Customer = Depends(get_current_customer),
    entity_type: Optional[str] = Query(None, description="Filter by entity type"),
    skip: int = Query(0, description="Number of items to skip"),
    limit: int = Query(20, description="Maximum number of items to return")
):
    """
    List all entities owned by the current user.
    Returns entities with their basic information and available workflows.
    """
    # Get user's entities from database
    entities = await EntityService.find_entities(
        owner_user_id=str(current_customer.id),
        entity_type=entity_type,
        skip=skip,
        limit=limit
    )
    
    # Transform entities for response
    result = []
    for entity in entities:
        # Get available workflows for this entity type
        all_workflows = await workflow_service.list_workflow_definitions(status="active")
        
        # Filter workflows that can work with this entity type
        # Look for workflows that have this entity type in their metadata
        available_workflows = []
        for w in all_workflows:
            if w.metadata and "entity_types" in w.metadata:
                if entity.entity_type in w.metadata["entity_types"]:
                    available_workflows.append({
                        "workflow_id": w.workflow_id,
                        "name": w.name,
                        "description": w.description,
                        "category": w.category
                    })
        
        entity_data = {
            "entity_id": entity.entity_id,
            "entity_type": entity.entity_type,
            "name": entity.name,
            "status": entity.status,
            "verified": entity.verified,
            "data": entity.data,
            "created_at": entity.created_at.isoformat() if entity.created_at else None,
            "updated_at": entity.updated_at.isoformat() if entity.updated_at else None,
            "available_workflows": available_workflows,
            "relationships_count": len([r for r in entity.relationships if r.is_active])
        }
        result.append(entity_data)
    
    # Get total count for pagination
    total_count = await LegalEntity.find(
        LegalEntity.owner_user_id == str(current_customer.id)
    ).count()
    
    return {
        "entities": result,
        "total": total_count,
        "skip": skip,
        "limit": limit
    }


@router.get("/entities/{entity_id}")
async def get_entity_details(
    entity_id: str,
    current_customer: Customer = Depends(get_current_customer),
    include_visualization: bool = Query(False, description="Include visualization config")
):
    """
    Get detailed information about a specific entity.
    """
    # Get the entity
    entity = await EntityService.get_entity(entity_id, str(current_customer.id))
    
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    
    # Get workflows that have been run with this entity
    instances = await WorkflowInstance.find(
        {"context.entity_id": entity_id}
    ).to_list()
    
    # Get available workflows for this entity type
    all_workflows = await workflow_service.list_workflow_definitions(status="active")
    available_workflows = []
    for w in all_workflows:
        if w.metadata and "entity_types" in w.metadata:
            if entity.entity_type in w.metadata["entity_types"]:
                available_workflows.append({
                    "workflow_id": w.workflow_id,
                    "name": w.name,
                    "description": w.description,
                    "category": w.category,
                    "estimatedDuration": w.metadata.get("estimated_duration", "10-20 min")
                })
    
    # Prepare entity data
    entity_dict = entity.to_display_dict()

    # Add visualization config if requested
    if include_visualization:
        entity_dict.update({
            "visualization_config": entity.visualization_config or {},
            "entity_display_config": entity.entity_display_config or {},
        })

    return {
        "entity": entity_dict,
        "available_workflows": available_workflows,
        "recent_instances": [
            {
                "instance_id": inst.instance_id,
                "workflow_id": inst.workflow_id,
                "status": inst.status,
                "created_at": inst.created_at.isoformat() if inst.created_at else None,
                "completed_at": inst.completed_at.isoformat() if inst.completed_at else None
            }
            for inst in instances[:10]  # Last 10 instances
        ]
    }


@router.post("/entities/{entity_id}/start-workflow")
async def start_entity_workflow(
    entity_id: str,
    workflow_id: str,
    current_customer: Customer = Depends(get_current_customer),
    initial_data: Dict[str, Any] = {}
):
    """
    Start a workflow instance for a specific entity.
    The entity data will be pre-populated in the workflow context.
    """
    # Verify entity ownership
    entity = await EntityService.get_entity(entity_id, str(current_customer.id))
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    
    # Get workflow definition
    workflow = await workflow_service.get_workflow_definition(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Check if workflow supports this entity type
    if workflow.metadata and "entity_types" in workflow.metadata:
        if entity.entity_type not in workflow.metadata["entity_types"]:
            raise HTTPException(
                status_code=400, 
                detail=f"Workflow {workflow_id} does not support entity type {entity.entity_type}"
            )
    
    # Prepare initial data with entity information
    workflow_data = {
        "entity_id": entity.entity_id,
        "entity_type": entity.entity_type,
        "entity_data": entity.data,
        **initial_data  # Allow additional data to be passed
    }
    
    # Create workflow instance
    dag_instance = await workflow_service.create_instance(
        workflow_id=workflow_id,
        user_id=str(current_customer.id),
        initial_data=workflow_data
    )
    
    # Start execution
    await workflow_service.execute_instance(dag_instance.instance_id)
    
    # Track that this entity was used in a workflow
    entity.used_in_workflows.append(dag_instance.instance_id)
    await entity.save()
    
    return {
        "success": True,
        "instance_id": dag_instance.instance_id,
        "workflow_id": workflow_id,
        "entity_id": entity_id,
        "status": "started",
        "tracking_url": f"/track/{dag_instance.instance_id}",
        "message": f"Workflow {workflow.name} started for {entity.name}"
    }


@router.delete("/entities/{entity_id}")
async def delete_entity(
    entity_id: str,
    current_customer: Customer = Depends(get_current_customer)
):
    """
    Delete an entity owned by the current user.
    This will also delete all relationships and associated data.
    """
    logger = logging.getLogger(__name__)

    # Set workflow context for structured logging
    set_workflow_context(
        user_id=str(current_customer.id),
        tenant=getattr(current_customer, 'tenant', None)
    )

    logger.info("🔐 Entity deletion request received", extra={
        "entity_id": entity_id,
        "user_id": str(current_customer.id),
        "action": "delete_entity_request"
    })

    try:
        # Verify entity ownership
        entity = await EntityService.get_entity(entity_id, str(current_customer.id))
        if not entity:
            logger.warning("Entity not found or access denied", extra={
                "entity_id": entity_id,
                "user_id": str(current_customer.id),
                "action": "entity_not_found"
            })
            raise HTTPException(status_code=404, detail="Entity not found")

        logger.debug("Entity found, proceeding with deletion", extra={
            "entity_id": entity_id,
            "entity_type": entity.entity_type,
            "entity_name": entity.name,
            "action": "entity_found"
        })

        # Set tenant context in EntityService if available
        if hasattr(current_customer, 'tenant'):
            set_workflow_context(
                user_id=str(current_customer.id),
                tenant=current_customer.tenant
            )

        # Delete the entity (this will handle workflow checks and relationships internally)
        success = await EntityService.delete_entity(entity_id, str(current_customer.id))

        if not success:
            logger.error("EntityService returned false for deletion", extra={
                "entity_id": entity_id,
                "user_id": str(current_customer.id),
                "action": "deletion_service_failed"
            })
            raise HTTPException(status_code=500, detail="Failed to delete entity")

        logger.info("✅ Entity deletion API completed successfully", extra={
            "entity_id": entity_id,
            "entity_name": entity.name,
            "user_id": str(current_customer.id),
            "action": "delete_entity_success"
        })

        return {
            "success": True,
            "message": f"Entity '{entity.name}' deleted successfully",
            "deleted_entity_id": entity_id
        }

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except ValueError as ve:
        # Handle business logic errors (like active workflows)
        logger.warning("Entity deletion blocked by business rules", extra={
            "entity_id": entity_id,
            "user_id": str(current_customer.id),
            "error_message": str(ve),
            "action": "deletion_blocked"
        })
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error("❌ Unexpected error during entity deletion: %s", str(e), extra={
            "entity_id": entity_id,
            "user_id": str(current_customer.id),
            "error_message": str(e),
            "error_type": type(e).__name__,
            "action": "delete_entity_error"
        }, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete entity")


@router.get("/entities/{entity_id}/documents")
async def get_entity_documents(
    entity_id: str,
    current_customer: Customer = Depends(get_current_customer)
):
    """
    Get all documents linked to a specific entity.
    """
    # Verify entity ownership
    entity = await EntityService.get_entity(entity_id, str(current_customer.id))
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    
    # Get document relationships
    document_relationships = entity.get_relationships(
        relationship_type="has_document",
        active_only=True
    )
    
    # Fetch actual document entities
    documents = []
    for rel in document_relationships:
        doc = await EntityService.get_entity(rel.to_entity_id)
        if doc and doc.entity_type == "document":
            documents.append({
                "document_id": doc.entity_id,
                "name": doc.name,
                "document_type": doc.data.get("document_type"),
                "document_subtype": doc.data.get("document_subtype"),
                "document_number": doc.data.get("document_number"),
                "issued_date": doc.data.get("issued_date"),
                "expiry_date": doc.data.get("expiry_date"),
                "issuing_authority": doc.data.get("issuing_authority"),
                "verification_status": doc.data.get("verification_status"),
                "is_expired": _is_document_expired(doc.data.get("expiry_date")),
                "created_at": doc.created_at.isoformat() if doc.created_at else None,
                "metadata": doc.data
            })
    
    return {
        "entity_id": entity_id,
        "documents": documents,
        "total": len(documents)
    }


def _is_document_expired(expiry_date_str: Optional[str]) -> bool:
    """Check if a document is expired based on expiry date."""
    if not expiry_date_str:
        return False
    try:
        expiry_date = datetime.fromisoformat(expiry_date_str.replace('Z', '+00:00'))
        return expiry_date < datetime.utcnow()
    except:
        return False


@router.get("/entity-types")
async def get_entity_types():
    """
    Get all available entity types in the system.
    """
    entity_types = await EntityService.list_entity_types()
    
    return {
        "entity_types": [
            {
                "type_id": et.type_id,
                "name": et.name,
                "alias": et.alias,
                "description": et.description,
                "icon": et.icon,
                "color": et.color
            }
            for et in entity_types
        ]
    }


def _extract_entity_requirements(dag: DAG) -> List[Dict[str, Any]]:
    """
    Extract entity requirements from DAG tasks that have entity operators.
    Returns list of entity requirement info for display.
    """
    entity_requirements = []

    if not dag or not dag.tasks:
        return entity_requirements

    for task_id, task in dag.tasks.items():
        # Check for EntityPickerOperator and MultiEntityRequirementOperator
        if hasattr(task, 'requirements') and task.requirements:
            for req in task.requirements:
                # Only include requirements that have info for display
                if isinstance(req, dict) and req.get('info'):
                    entity_requirements.append(req['info'])

    return entity_requirements


async def _get_workflow_data(workflow: WorkflowDefinition, dag: Optional[DAG], locale: str) -> Dict[str, Any]:
    """Extract all workflow data from definition and DAG."""
    steps = []
    
    # Prefer DAG data over database data
    if dag:
        # Get workflow info from DAG (it has the most up-to-date data)
        name = dag.name if hasattr(dag, 'name') else workflow.name
        description = dag.description if dag.description else workflow.description
        category = dag.category if hasattr(dag, 'category') else (workflow.category or "general")
        tags = dag.tags if dag.tags else (workflow.tags or [])
        metadata = dag.metadata if hasattr(dag, 'metadata') and dag.metadata else (workflow.metadata or {})
        
        # Get steps from DAG
        for task_id, task in dag.tasks.items():
            # Use custom name from kwargs if provided, otherwise generate from task_id
            task_name = task.kwargs.get('name') if hasattr(task, 'kwargs') and 'name' in task.kwargs else task_id.replace("_", " ").title()

            step_data = {
                "id": task_id,
                "name": task_name,
                "type": task.__class__.__name__
            }
            # Add description if available
            if hasattr(task, 'description'):
                step_data["description"] = task.description
            
            # Add form_config for UserInputOperator
            if hasattr(task, 'form_config') and task.form_config:
                step_data["form_config"] = task.form_config
            
            # Add any other operator-specific configuration
            if hasattr(task, 'kwargs') and task.kwargs:
                # Check for specific kwargs that should be exposed
                if 'form_config' in task.kwargs:
                    step_data["form_config"] = task.kwargs['form_config']

            # Add requirements for this step if it has them
            if hasattr(task, 'requirements') and task.requirements:
                step_data["requirements"] = task.requirements

            steps.append(step_data)
    else:
        # Fall back to database data if no DAG
        name = workflow.name
        description = workflow.description or ""
        category = workflow.category or "general"
        tags = workflow.tags or []
        metadata = workflow.metadata or {}
    
    # Try 2: Get steps from database if no DAG
    if not steps:
        from app.models.workflow import WorkflowStep
        db_steps = await WorkflowStep.find(
            WorkflowStep.workflow_id == workflow.workflow_id
        ).to_list()
        
        for step in db_steps:
            step_data = {
                "id": step.step_id,
                "name": step.name,
                "type": step.step_type,
                "description": step.description or step.name
            }
            steps.append(step_data)
    
    # Try 3: Get steps from registry if still no steps
    # Registry is deprecated - using DAG system instead
    # if not steps:
    #     from app.workflows.registry import step_registry
    #     if workflow.workflow_id in step_registry.workflows:
    #         wf = step_registry.workflows[workflow.workflow_id]
    #         for step_id, step in wf.steps.items():
    #             step_data = {
    #                 "id": step_id,
    #                 "name": step.name,
    #                 "type": step.__class__.__name__,
    #                 "description": step.description if hasattr(step, 'description') else step.name
    #             }
    #             steps.append(step_data)
    
    return {
        "id": workflow.workflow_id,
        "workflow_id": workflow.workflow_id,
        "name": name,
        "title": name,
        "description": description,
        "category": category,
        "icon": metadata.get("icon") or _get_workflow_icon(category),
        "estimatedDuration": metadata.get("estimatedTime") or metadata.get("estimated_duration") or _calculate_duration(len(steps)),
        "steps": steps,
        "requirements": metadata.get("requirements", []) or _get_workflow_requirements(workflow, locale),
        "entity_requirements": _extract_entity_requirements(dag) if dag else [],
        "available": metadata.get("available", True) if "available" in metadata else workflow.status == "active",
        "tags": tags,
        "popularity": metadata.get("popularity", 0),
        "created_at": workflow.created_at.isoformat() if workflow.created_at else None,
        "updated_at": workflow.updated_at.isoformat() if workflow.updated_at else None,
        "metadata": metadata
    }