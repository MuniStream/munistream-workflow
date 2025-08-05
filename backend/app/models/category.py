from typing import Dict, Any, List, Optional
from datetime import datetime
from beanie import Document
from pydantic import Field
from pymongo import IndexModel


class WorkflowCategory(Document):
    """Workflow category definition for organizing trámites"""
    category_id: str = Field(..., description="Unique category identifier")
    name: str = Field(..., description="Category name")
    description: Optional[str] = Field(None, description="Category description")
    parent_category_id: Optional[str] = Field(None, description="Parent category ID for hierarchical organization")
    
    # Visual metadata
    icon: Optional[str] = Field(None, description="Icon identifier for UI")
    color: Optional[str] = Field(None, description="Color code for UI")
    display_order: int = Field(default=0, description="Display order in lists")
    
    # Category type for main groupings  
    category_type: str = Field(..., description="Type: rpp, catastro, vinculado")
    
    # Statistics
    workflow_count: int = Field(default=0, description="Number of workflows in this category")
    active_instances: int = Field(default=0, description="Number of active instances in this category")
    
    # Status
    is_active: bool = Field(default=True, description="Whether category is active")
    is_featured: bool = Field(default=False, description="Whether to feature this category")
    
    # Metadata
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional category metadata")
    
    # Audit fields
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = Field(None, description="User who created this category")
    updated_by: Optional[str] = Field(None, description="User who last updated this category")
    
    class Settings:
        name = "workflow_categories"
        indexes = [
            IndexModel([("category_id", 1)], unique=True),
            IndexModel([("name", 1)]),
            IndexModel([("category_type", 1)]),
            IndexModel([("parent_category_id", 1)]),
            IndexModel([("is_active", 1)]),
            IndexModel([("display_order", 1)]),
            IndexModel([("created_at", -1)]),
        ]