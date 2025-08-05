from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from datetime import datetime

from ...models.category import WorkflowCategory
from ...models.workflow import WorkflowDefinition
from ...schemas.category import (
    CategoryCreate,
    CategoryUpdate, 
    CategoryResponse,
    CategoryListResponse,
    CategoryWithStats
)

router = APIRouter()


@router.get("/", response_model=CategoryListResponse)
async def get_categories(
    category_type: Optional[str] = Query(None, description="Filter by category type (rpp, catastro, vinculado)"),
    parent_id: Optional[str] = Query(None, description="Filter by parent category ID"),
    is_active: bool = Query(True, description="Filter by active status"),
    include_stats: bool = Query(False, description="Include workflow count statistics"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Number of categories per page")
):
    """Get list of workflow categories with optional filtering"""
    
    # Build query filters
    query_filters = {"is_active": is_active}
    
    if category_type:
        query_filters["category_type"] = category_type
    
    if parent_id:
        query_filters["parent_category_id"] = parent_id
    
    # Get total count
    total = await WorkflowCategory.find(query_filters).count()
    
    # Get categories with pagination
    categories = await WorkflowCategory.find(query_filters).sort(
        [("display_order", 1), ("name", 1)]
    ).skip((page - 1) * page_size).limit(page_size).to_list()
    
    # Include statistics if requested
    if include_stats:
        category_data = []
        for category in categories:
            # Count workflows in this category (all statuses)
            workflow_count = await WorkflowDefinition.find(
                {"category": category.category_id}
            ).count()
            
            category_dict = category.dict()
            category_dict["workflow_count"] = workflow_count
            category_data.append(CategoryWithStats(**category_dict))
        
        return CategoryListResponse(
            categories=category_data,
            total=total,
            page=page,
            page_size=page_size
        )
    else:
        return CategoryListResponse(
            categories=[CategoryResponse(**cat.dict()) for cat in categories],
            total=total,
            page=page,
            page_size=page_size
        )


@router.get("/{category_id}", response_model=CategoryResponse)
async def get_category(category_id: str):
    """Get a specific category by ID"""
    category = await WorkflowCategory.find_one({"category_id": category_id})
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    return CategoryResponse(**category.dict())


@router.post("/", response_model=CategoryResponse)
async def create_category(category_data: CategoryCreate):
    """Create a new workflow category"""
    
    # Check if category_id already exists
    existing = await WorkflowCategory.find_one({"category_id": category_data.category_id})
    if existing:
        raise HTTPException(status_code=400, detail="Category ID already exists")
    
    # Check if parent category exists (if specified)
    if category_data.parent_category_id:
        parent = await WorkflowCategory.find_one({"category_id": category_data.parent_category_id})
        if not parent:
            raise HTTPException(status_code=400, detail="Parent category not found")
    
    # Create new category
    category = WorkflowCategory(**category_data.dict())
    await category.create()
    
    return CategoryResponse(**category.dict())


@router.put("/{category_id}", response_model=CategoryResponse)
async def update_category(category_id: str, category_data: CategoryUpdate):
    """Update an existing category"""
    
    category = await WorkflowCategory.find_one({"category_id": category_id})
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    # Check if parent category exists (if being updated)
    if category_data.parent_category_id:
        parent = await WorkflowCategory.find_one({"category_id": category_data.parent_category_id})
        if not parent:
            raise HTTPException(status_code=400, detail="Parent category not found")
        
        # Prevent circular references
        if category_data.parent_category_id == category_id:
            raise HTTPException(status_code=400, detail="Category cannot be its own parent")
    
    # Update fields
    update_data = category_data.dict(exclude_unset=True)
    update_data["updated_at"] = datetime.utcnow()
    
    for field, value in update_data.items():
        setattr(category, field, value)
    
    await category.save()
    
    return CategoryResponse(**category.dict())


@router.delete("/{category_id}")
async def delete_category(category_id: str):
    """Delete a category (soft delete by setting is_active=False)"""
    
    category = await WorkflowCategory.find_one({"category_id": category_id})
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    # Check if category has any workflows assigned
    workflow_count = await WorkflowDefinition.find({"category": category_id}).count()
    if workflow_count > 0:
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot delete category. {workflow_count} workflows are assigned to this category."
        )
    
    # Check if category has subcategories
    subcategory_count = await WorkflowCategory.find({"parent_category_id": category_id}).count()
    if subcategory_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete category. {subcategory_count} subcategories exist."
        )
    
    # Soft delete
    category.is_active = False
    category.updated_at = datetime.utcnow()
    await category.save()
    
    return {"message": "Category deleted successfully"}


@router.get("/{category_id}/workflows")
async def get_category_workflows(
    category_id: str,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Number of workflows per page")
):
    """Get all workflows in a specific category"""
    
    # Verify category exists
    category = await WorkflowCategory.find_one({"category_id": category_id})
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    # Get workflows in this category
    total = await WorkflowDefinition.find({"category": category_id}).count()
    
    workflows = await WorkflowDefinition.find({"category": category_id}).sort(
        "name", 1
    ).skip((page - 1) * page_size).limit(page_size).to_list()
    
    return {
        "category": CategoryResponse(**category.dict()),
        "workflows": workflows,
        "total": total,
        "page": page,
        "page_size": page_size
    }


@router.post("/initialize-default-categories")
async def initialize_default_categories():
    """Initialize default categories for PUENTE workflows"""
    
    default_categories = [
        # RPP Categories
        {
            "category_id": "rpp_inscripciones",
            "name": "Inscripciones",
            "description": "Trámites de inscripción en el Registro Público de la Propiedad",
            "category_type": "rpp",
            "icon": "edit",
            "color": "#2563eb",
            "display_order": 1
        },
        {
            "category_id": "rpp_gravamenes", 
            "name": "Gravámenes",
            "description": "Trámites relacionados con gravámenes y limitaciones de dominio",
            "category_type": "rpp",
            "icon": "lock",
            "color": "#dc2626",
            "display_order": 2
        },
        {
            "category_id": "rpp_cancelaciones",
            "name": "Cancelaciones", 
            "description": "Cancelación de inscripciones y gravámenes",
            "category_type": "rpp",
            "icon": "x-circle",
            "color": "#ea580c",
            "display_order": 3
        },
        {
            "category_id": "rpp_certificados",
            "name": "Certificados",
            "description": "Emisión de certificados registrales",
            "category_type": "rpp", 
            "icon": "award",
            "color": "#059669",
            "display_order": 4
        },
        
        # Catastro Categories
        {
            "category_id": "catastro_registro",
            "name": "Registro",
            "description": "Registro de predios y cambios de propietario",
            "category_type": "catastro",
            "icon": "home",
            "color": "#7c3aed",
            "display_order": 5
        },
        {
            "category_id": "catastro_traslados",
            "name": "Traslados",
            "description": "Traslados de dominio y cambios de titularidad",
            "category_type": "catastro",
            "icon": "arrow-right",
            "color": "#0891b2",
            "display_order": 6
        },
        {
            "category_id": "catastro_valuacion",
            "name": "Valuación",
            "description": "Avalúos y actualizaciones de valores catastrales",
            "category_type": "catastro",
            "icon": "calculator",
            "color": "#ca8a04",
            "display_order": 7
        },
        {
            "category_id": "catastro_certificados",
            "name": "Certificados",
            "description": "Certificados de valor catastral y predial",
            "category_type": "catastro",
            "icon": "document",
            "color": "#059669",
            "display_order": 8
        },
        
        # Vinculados Categories
        {
            "category_id": "vinculados",
            "name": "Trámites Vinculados",
            "description": "Procesos que actualizan RPP y Catastro simultáneamente",
            "category_type": "vinculado",
            "icon": "link",
            "color": "#9333ea",
            "display_order": 9,
            "is_featured": True
        }
    ]
    
    created_count = 0
    
    for cat_data in default_categories:
        # Check if category already exists
        existing = await WorkflowCategory.find_one({"category_id": cat_data["category_id"]})
        if not existing:
            category = WorkflowCategory(**cat_data)
            await category.create()
            created_count += 1
    
    return {
        "message": f"Initialized {created_count} default categories",
        "total_categories": len(default_categories)
    }


@router.post("/assign-workflows-to-categories")
async def assign_workflows_to_categories():
    """Assign existing workflows to appropriate categories based on their names and types"""
    
    # Mapping of workflow patterns to category IDs
    workflow_category_mapping = {
        # RPP workflows
        "rpp_inscripcion": "rpp_inscripciones",
        "inscripcion_compraventa": "rpp_inscripciones", 
        "inscripcion_escritura": "rpp_inscripciones",
        "gravamen": "rpp_gravamenes",
        "hipoteca": "rpp_gravamenes",
        "cancelacion": "rpp_cancelaciones",
        "certificado": "rpp_certificados",
        
        # Catastro workflows
        "catastro_alta": "catastro_registro",
        "alta_predio": "catastro_registro",
        "traslado_dominio": "catastro_traslados",
        "avaluo": "catastro_valuacion",
        "valor_catastral": "catastro_valuacion",
        
        # Vinculados workflows
        "unificado": "vinculados",
        "vinculado": "vinculados",
        "certificado_libertad_gravamen": "vinculados",  # Este es un proceso vinculado
        "actualizacion_catastral_unificada": "vinculados",
        "traslado_dominio_unificado": "vinculados"
    }
    
    # Get all workflows
    workflows = await WorkflowDefinition.find().to_list()
    
    assigned_count = 0
    assignments = []
    
    for workflow in workflows:
        workflow_id = workflow.workflow_id.lower()
        workflow_name = workflow.name.lower()
        
        # Find matching category
        assigned_category = None
        
        for pattern, category_id in workflow_category_mapping.items():
            if pattern in workflow_id or pattern in workflow_name:
                assigned_category = category_id
                break
        
        # Special logic for specific workflows
        if not assigned_category:
            if "catastro" in workflow_id and "certificado" in workflow_id:
                assigned_category = "catastro_certificados"
            elif "rpp" in workflow_id and "certificado" in workflow_id:
                assigned_category = "rpp_certificados"
            elif "catastro" in workflow_id:
                assigned_category = "catastro_registro"
            elif "rpp" in workflow_id:
                assigned_category = "rpp_inscripciones"
        
        # Assign category if found
        if assigned_category:
            workflow.category = assigned_category
            await workflow.save()
            assigned_count += 1
            assignments.append({
                "workflow_id": workflow.workflow_id,
                "workflow_name": workflow.name,
                "assigned_category": assigned_category
            })
    
    # Update category workflow counts
    categories = await WorkflowCategory.find().to_list()
    for category in categories:
        count = await WorkflowDefinition.find({"category": category.category_id}).count()
        category.workflow_count = count
        await category.save()
    
    return {
        "message": f"Assigned {assigned_count} workflows to categories",
        "total_workflows": len(workflows),
        "assignments": assignments
    }