"""
Example workflow for property registration using Legal Entities.
This workflow demonstrates how to use entity operators.
"""
from datetime import datetime
from typing import Dict, Any

from ..dag import DAG
from ..operators.user_input import UserInputOperator
from ..operators.entity_operators import (
    EntityRequirementOperator,
    EntityCreationOperator,
    EntityRelationshipOperator,
    EntityValidationOperator
)
from ..operators.action import ActionOperator
from ..operators.terminal import TerminalOperator


def create_property_registration_workflow() -> DAG:
    """
    Create a workflow for registering a property.
    
    This workflow:
    1. Checks if user has a person entity (owner)
    2. Collects property information
    3. Creates a property entity
    4. Creates ownership relationship
    5. Validates everything
    """
    
    with DAG(
        dag_id="property_registration_v1",
        description="Registro de Propiedad con Entidades Legales",
        tags=["property", "registration", "entities"],
        metadata={
            "category": "property",
            "estimated_duration": "15 minutes",
            "requires_entities": ["person"]
        }
    ) as dag:
        
        # Step 1: Check if user has a person entity (the owner)
        check_owner = EntityRequirementOperator(
            task_id="check_owner",
            required_entities=[
                {
                    "type": "person",
                    "min": 1,
                    "filters": {}  # No specific filters, any person entity
                }
            ],
            allow_creation=True
        )
        
        # Step 2: Collect property information from user
        collect_property_info = UserInputOperator(
            task_id="collect_property_info",
            form_config={
                "title": "Información de la Propiedad",
                "description": "Por favor proporcione los datos de la propiedad a registrar",
                "fields": [
                    {
                        "name": "predial_id",
                        "type": "text",
                        "label": "Clave Catastral/Predial",
                        "required": True,
                        "placeholder": "Ej: 123-456-789",
                        "validation": {
                            "pattern": "^[0-9]{3}-[0-9]{3}-[0-9]{3}$",
                            "message": "Formato: XXX-XXX-XXX"
                        }
                    },
                    {
                        "name": "property_type",
                        "type": "select",
                        "label": "Tipo de Propiedad",
                        "required": True,
                        "options": [
                            {"value": "casa", "label": "Casa"},
                            {"value": "departamento", "label": "Departamento"},
                            {"value": "terreno", "label": "Terreno"},
                            {"value": "local", "label": "Local Comercial"}
                        ]
                    },
                    {
                        "name": "address",
                        "type": "object",
                        "label": "Dirección",
                        "required": True,
                        "fields": [
                            {"name": "street", "type": "text", "label": "Calle", "required": True},
                            {"name": "number", "type": "text", "label": "Número", "required": True},
                            {"name": "colony", "type": "text", "label": "Colonia", "required": True},
                            {"name": "city", "type": "text", "label": "Ciudad", "required": True},
                            {"name": "state", "type": "text", "label": "Estado", "required": True},
                            {"name": "zip_code", "type": "text", "label": "Código Postal", "required": True}
                        ]
                    },
                    {
                        "name": "surface_area",
                        "type": "number",
                        "label": "Superficie del Terreno (m²)",
                        "required": True,
                        "min": 1,
                        "max": 100000
                    },
                    {
                        "name": "construction_area",
                        "type": "number",
                        "label": "Superficie de Construcción (m²)",
                        "required": False,
                        "min": 0,
                        "max": 100000
                    },
                    {
                        "name": "use_type",
                        "type": "select",
                        "label": "Uso de Suelo",
                        "required": True,
                        "options": [
                            {"value": "habitacional", "label": "Habitacional"},
                            {"value": "comercial", "label": "Comercial"},
                            {"value": "industrial", "label": "Industrial"},
                            {"value": "mixto", "label": "Mixto"}
                        ]
                    }
                ]
            },
            required_fields=["predial_id", "property_type", "address", "surface_area", "use_type"]
        )
        
        # Step 3: Validate predial ID doesn't already exist
        validate_predial = ActionOperator(
            task_id="validate_predial",
            action=lambda ctx: validate_predial_uniqueness(ctx)
        )
        
        # Step 4: Create the property entity
        create_property = EntityCreationOperator(
            task_id="create_property",
            entity_type="property",
            name_field="property_display_name",  # Will be generated in previous step
            data_source="context",
            data_mapping={
                "collect_property_info_data.predial_id": "predial_id",
                "collect_property_info_data.property_type": "property_type",
                "collect_property_info_data.address": "address",
                "collect_property_info_data.surface_area": "surface_area",
                "collect_property_info_data.construction_area": "construction_area",
                "collect_property_info_data.use_type": "use_type",
                "property_legal_status": "legal_status"
            },
            auto_verify=False  # Will be verified after validation
        )
        
        # Step 5: Create ownership relationship
        create_ownership = EntityRelationshipOperator(
            task_id="create_ownership",
            from_entity_field="check_owner_entities.person",  # Owner entity ID
            to_entity_field="create_property_entity_id",  # Property entity ID
            relationship_type="owns",
            metadata_fields={
                "registration_date": "current_date",
                "registration_type": "registration_type"
            }
        )
        
        # Step 6: Validate the created entities
        validate_entities = EntityValidationOperator(
            task_id="validate_entities",
            entity_id_field="create_property_entity_id",
            validations=["type_definition"],
            require_verified=False,
            require_relationships=["owns"]
        )
        
        # Step 7: Generate registration certificate
        generate_certificate = ActionOperator(
            task_id="generate_certificate",
            action=lambda ctx: generate_property_certificate(ctx)
        )
        
        # Terminal step
        complete = TerminalOperator(
            task_id="complete",
            terminal_status="SUCCESS",
            terminal_message="Propiedad registrada exitosamente"
        )
        
        # Define flow
        check_owner >> collect_property_info >> validate_predial >> create_property
        create_property >> create_ownership >> validate_entities >> generate_certificate >> complete
    
    return dag


def validate_predial_uniqueness(context: Dict[str, Any]) -> Dict[str, Any]:
    """Validate that the predial ID is unique"""
    property_data = context.get("collect_property_info_data", {})
    predial_id = property_data.get("predial_id")
    
    # In a real implementation, this would check the database
    # For now, we'll simulate validation
    
    # Generate a display name for the property
    address = property_data.get("address", {})
    display_name = f"{address.get('street', '')} {address.get('number', '')}, {address.get('colony', '')}"
    
    return {
        "property_display_name": display_name,
        "property_legal_status": "regularizado",
        "registration_type": "primera_inscripcion",
        "current_date": datetime.utcnow().isoformat()
    }


def generate_property_certificate(context: Dict[str, Any]) -> Dict[str, Any]:
    """Generate a registration certificate for the property"""
    property_id = context.get("create_property_entity_id")
    owner_entities = context.get("check_owner_entities", {})
    
    # In a real implementation, this would generate a PDF certificate
    certificate_data = {
        "certificate_id": f"CERT-{datetime.utcnow().strftime('%Y%m%d')}-{property_id[:8]}",
        "property_id": property_id,
        "owner_id": owner_entities.get("person", ["unknown"])[0],
        "registration_date": datetime.utcnow().isoformat(),
        "status": "registered",
        "document_url": f"/certificates/property/{property_id}.pdf"
    }
    
    return {
        "certificate": certificate_data,
        "registration_complete": True
    }


def get_property_workflows():
    """Get all property-related workflows"""
    return {
        "property_registration": create_property_registration_workflow()
    }