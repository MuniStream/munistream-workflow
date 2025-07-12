"""
Public API endpoints that don't require authentication.
These endpoints are used by the citizen portal for browsing workflows.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
from datetime import datetime

from ...workflows.registry import step_registry

router = APIRouter()


@router.get("/workflows")
async def get_public_workflows(
    category: Optional[str] = Query(None, description="Filter by category"),
    query: Optional[str] = Query(None, description="Search query"),
    sort_by: Optional[str] = Query("name", description="Sort by: name, duration, popularity"),
    sort_order: Optional[str] = Query("asc", description="Sort order: asc, desc")
):
    """Get all public workflows available for citizens to browse"""
    
    # Get workflows from registry (these are the coded workflows)
    registry_workflows = step_registry.list_workflows()
    
    # Convert to public format with additional metadata
    public_workflows = []
    
    for workflow_info in registry_workflows:
        workflow = step_registry.get_workflow(workflow_info["workflow_id"])
        if not workflow:
            continue
            
        # Determine category based on workflow type
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
        
        workflow_data = {
            "id": workflow.workflow_id,
            "name": workflow.name,
            "description": workflow.description,
            "category": workflow_category,
            "estimatedDuration": estimated_duration,
            "requirements": requirements[:5],  # Limit to 5 requirements
            "steps": public_steps,
            "isActive": True,
            "createdAt": datetime.now().isoformat(),
            "updatedAt": datetime.now().isoformat()
        }
        
        # Apply filters
        if category and workflow_category.lower() != category.lower():
            continue
            
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
async def get_featured_workflows():
    """Get featured/popular workflows for the homepage"""
    
    # Get all workflows and select featured ones
    all_workflows = await get_public_workflows(None, None, None, None)
    
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
async def get_public_workflow_detail(workflow_id: str):
    """Get detailed information about a specific workflow"""
    
    # Get workflow from registry
    workflow = step_registry.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
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
    
    return {
        "id": workflow.workflow_id,
        "name": workflow.name,
        "description": workflow.description,
        "category": workflow_category,
        "estimatedDuration": estimated_duration,
        "requirements": requirements,
        "steps": detailed_steps,
        "isActive": True,
        "createdAt": datetime.now().isoformat(),
        "updatedAt": datetime.now().isoformat()
    }


@router.get("/workflow-categories")
async def get_workflow_categories():
    """Get available workflow categories with counts"""
    
    # Get all workflows to count by category
    registry_workflows = step_registry.list_workflows()
    
    category_counts = {
        "Permits": 0,
        "Licenses": 0, 
        "Registration": 0,
        "Complaints": 0,
        "Renewals": 0,
        "General": 0
    }
    
    # Count workflows by category
    for workflow_info in registry_workflows:
        workflow_id = workflow_info["workflow_id"].lower()
        
        if "permit" in workflow_id:
            category_counts["Permits"] += 1
        elif "license" in workflow_id:
            category_counts["Licenses"] += 1
        elif "registration" in workflow_id:
            category_counts["Registration"] += 1
        elif "complaint" in workflow_id:
            category_counts["Complaints"] += 1
        elif "renewal" in workflow_id:
            category_counts["Renewals"] += 1
        else:
            category_counts["General"] += 1
    
    categories = []
    for category, count in category_counts.items():
        if count > 0:  # Only include categories with workflows
            categories.append({
                "id": category.lower(),
                "name": category,
                "description": f"{category} related government services",
                "workflowCount": count
            })
    
    return categories