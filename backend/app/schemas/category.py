from typing import Dict, Any, List, Optional, Union
from datetime import datetime
from pydantic import BaseModel, Field


class CategoryBase(BaseModel):
    name: str = Field(..., description="Category name")
    description: Optional[str] = Field(None, description="Category description")
    parent_category_id: Optional[str] = Field(None, description="Parent category ID")
    icon: Optional[str] = Field(None, description="Icon identifier")
    color: Optional[str] = Field(None, description="Color code")
    display_order: int = Field(default=0, description="Display order")
    category_type: str = Field(..., description="Category type: rpp, catastro, vinculado")
    is_active: bool = Field(default=True, description="Whether category is active")
    is_featured: bool = Field(default=False, description="Whether category is featured")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class CategoryCreate(CategoryBase):
    category_id: str = Field(..., description="Unique category identifier")


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(None, description="Category name")
    description: Optional[str] = Field(None, description="Category description")
    parent_category_id: Optional[str] = Field(None, description="Parent category ID")
    icon: Optional[str] = Field(None, description="Icon identifier")
    color: Optional[str] = Field(None, description="Color code")
    display_order: Optional[int] = Field(None, description="Display order")
    category_type: Optional[str] = Field(None, description="Category type")
    is_active: Optional[bool] = Field(None, description="Whether category is active")
    is_featured: Optional[bool] = Field(None, description="Whether category is featured")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class CategoryResponse(CategoryBase):
    category_id: str = Field(..., description="Category identifier")
    workflow_count: int = Field(default=0, description="Number of workflows in category")
    active_instances: int = Field(default=0, description="Number of active instances")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    created_by: Optional[str] = Field(None, description="Creator user ID")
    updated_by: Optional[str] = Field(None, description="Last updater user ID")

    class Config:
        from_attributes = True


class CategoryWithStats(CategoryResponse):
    """Category response with computed statistics"""
    workflow_count: int = Field(..., description="Actual count of workflows in this category")
    

class CategoryListResponse(BaseModel):
    categories: List[Union[CategoryResponse, CategoryWithStats]]
    total: int
    page: int = 1
    page_size: int = 20


class CategoryHierarchy(BaseModel):
    """Hierarchical representation of categories"""
    category: CategoryResponse
    subcategories: List['CategoryHierarchy'] = Field(default_factory=list)
    workflow_count: int = Field(default=0, description="Total workflows including subcategories")


# Fix forward reference
CategoryHierarchy.model_rebuild()


class CategoryStats(BaseModel):
    """Category statistics"""
    category_id: str
    category_name: str
    workflow_count: int
    active_instances: int
    completed_instances: int
    failed_instances: int
    avg_completion_time: Optional[float] = Field(None, description="Average completion time in hours")
    
    
class CategoryStatsResponse(BaseModel):
    """Response for category statistics"""
    stats: List[CategoryStats]
    total_categories: int
    generated_at: datetime = Field(default_factory=datetime.utcnow)