"""
Simplified Legal Entity System - Completely Agnostic
Entities are just types with data, workflows define what they need.
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field
from beanie import Document, Indexed


class EntityType(Document):
    """
    Simple entity type definition - just a name and alias.
    No predefined fields or structure.
    """
    type_id: Indexed(str, unique=True)  # e.g., "person", "property", "vehicle"
    name: str  # Display name, e.g., "Persona"
    alias: Optional[str] = None  # Alternative name
    description: Optional[str] = None
    icon: Optional[str] = None  # Optional icon for UI
    color: Optional[str] = None  # Optional color for UI
    
    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    is_active: bool = True
    
    class Settings:
        name = "entity_types"


class EntityRelationship(BaseModel):
    """Simple relationship between two entities"""
    relationship_id: str = Field(default_factory=lambda: str(datetime.utcnow().timestamp()))
    relationship_type: str  # e.g., "owns", "manages", "parent_of"
    from_entity_id: str
    to_entity_id: str
    
    # Optional temporal data
    start_date: Optional[datetime] = Field(default_factory=datetime.utcnow)
    end_date: Optional[datetime] = None
    is_active: bool = True
    
    # Any additional data the workflow wants to store
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None


class LegalEntity(Document):
    """
    Completely agnostic legal entity.
    Just stores type and data - structure is defined by workflows.
    """
    entity_id: Indexed(str, unique=True)
    entity_type: Indexed(str)  # References EntityType.type_id
    owner_user_id: Indexed(str)  # User who owns this entity
    
    # Display name for the entity
    name: str
    
    # Completely flexible data storage - workflows decide structure
    data: Dict[str, Any] = Field(default_factory=dict)
    
    # Relationships to other entities
    relationships: List[EntityRelationship] = Field(default_factory=list)
    
    # Status and verification (optional, workflow-driven)
    status: str = "active"
    verified: bool = False
    verification_date: Optional[datetime] = None
    verified_by: Optional[str] = None
    
    # Tracking
    created_by_workflow: Optional[str] = None  # Workflow instance that created this
    used_in_workflows: List[str] = Field(default_factory=list)  # Workflow instances that used this

    # Visualization Configuration
    visualization_config: Optional[Dict[str, Any]] = None  # Field-level visualization hints
    entity_display_config: Optional[Dict[str, Any]] = None  # Entity-level display configuration

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Settings:
        name = "legal_entities"
        indexes = [
            "entity_type",
            "owner_user_id",
            "status",
            [("owner_user_id", 1), ("entity_type", 1)],
            [("entity_id", 1), ("owner_user_id", 1)]
        ]
    
    def add_relationship(
        self,
        to_entity_id: str,
        relationship_type: str,
        metadata: Dict[str, Any] = None
    ) -> EntityRelationship:
        """Add a relationship to another entity"""
        relationship = EntityRelationship(
            relationship_type=relationship_type,
            from_entity_id=self.entity_id,
            to_entity_id=to_entity_id,
            metadata=metadata or {}
        )
        self.relationships.append(relationship)
        self.updated_at = datetime.utcnow()
        return relationship
    
    def get_relationships(
        self,
        relationship_type: Optional[str] = None,
        active_only: bool = True
    ) -> List[EntityRelationship]:
        """Get relationships, optionally filtered"""
        rels = self.relationships
        
        if active_only:
            rels = [r for r in rels if r.is_active]
        
        if relationship_type:
            rels = [r for r in rels if r.relationship_type == relationship_type]
        
        return rels
    
    def update_data(self, new_data: Dict[str, Any], merge: bool = True):
        """Update entity data"""
        if merge:
            self.data.update(new_data)
        else:
            self.data = new_data
        self.updated_at = datetime.utcnow()
    
    def get_data_field(self, field_path: str, default: Any = None) -> Any:
        """
        Get a field from data using dot notation.
        e.g., "address.city" returns data["address"]["city"]
        """
        keys = field_path.split(".")
        value = self.data
        
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
                if value is None:
                    return default
            else:
                return default
        
        return value
    
    def to_display_dict(self) -> Dict[str, Any]:
        """Simple display representation"""
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "name": self.name,
            "owner_user_id": self.owner_user_id,
            "status": self.status,
            "verified": self.verified,
            "data": self.data,
            "relationships_count": len([r for r in self.relationships if r.is_active]),
            "created_at": self.created_at.isoformat() if self.created_at else None
        }