"""
Simple test workflow for entity system.
Creates a person entity and then a property with ownership relationship.
"""
from ..dag import DAG
from ..operators.user_input import UserInputOperator
from ..operators.entity_operators import (
    EntityCreationOperator,
    EntityRequirementOperator,
    EntityRelationshipOperator
)
from ..operators.python import PythonOperator


def create_test_entity_workflow() -> DAG:
    """
    Simple workflow to test entity creation.
    1. Collect person data
    2. Create person entity  
    3. Collect property data
    4. Create property entity
    5. Create ownership relationship
    """
    
    with DAG(
        dag_id="test_entity_workflow_v1",
        description="Test workflow for entity system",
        tags=["test", "entities"]
    ) as dag:
        
        # Step 1: Collect person data
        collect_person = UserInputOperator(
            task_id="collect_person",
            form_config={
                "title": "Person Information",
                "fields": [
                    {
                        "name": "first_name",
                        "type": "text",
                        "label": "First Name",
                        "required": True
                    },
                    {
                        "name": "last_name",
                        "type": "text", 
                        "label": "Last Name",
                        "required": True
                    },
                    {
                        "name": "email",
                        "type": "email",
                        "label": "Email",
                        "required": False
                    }
                ]
            },
            required_fields=["first_name", "last_name"]
        )
        
        # Step 2: Create person entity
        create_person = EntityCreationOperator(
            task_id="create_person",
            entity_type="person",
            name_source="person_display_name",  # Will be set from context
            data_mapping={
                "collect_person_data.first_name": "first_name",
                "collect_person_data.last_name": "last_name",
                "collect_person_data.email": "email"
            }
        )
        
        # Step 3: Collect property data
        collect_property = UserInputOperator(
            task_id="collect_property",
            form_config={
                "title": "Property Information",
                "fields": [
                    {
                        "name": "address",
                        "type": "text",
                        "label": "Address",
                        "required": True
                    },
                    {
                        "name": "property_type",
                        "type": "select",
                        "label": "Type",
                        "required": True,
                        "options": [
                            {"value": "house", "label": "House"},
                            {"value": "apartment", "label": "Apartment"},
                            {"value": "land", "label": "Land"}
                        ]
                    }
                ]
            },
            required_fields=["address", "property_type"]
        )
        
        # Step 4: Create property entity
        create_property = EntityCreationOperator(
            task_id="create_property",
            entity_type="property",
            name_source="property_display_name",
            data_mapping={
                "collect_property_data.address": "address",
                "collect_property_data.property_type": "type"
            }
        )
        
        # Step 5: Create ownership relationship
        create_ownership = EntityRelationshipOperator(
            task_id="create_ownership",
            from_entity_field="create_person_entity_id",
            to_entity_field="create_property_entity_id",
            relationship_type="owns"
        )
        
        # Terminal step using PythonOperator
        def complete_workflow(context):
            """Complete the workflow with summary"""
            return {
                "status": "SUCCESS",
                "message": "Entities created successfully",
                "person_entity": context.get("create_person_entity_id"),
                "property_entity": context.get("create_property_entity_id")
            }
        
        complete = PythonOperator(
            task_id="complete",
            python_callable=complete_workflow
        )
        
        # Define flow - but first we need to prepare display names
        def prepare_display_names(context):
            """Prepare display names for entities"""
            person_data = context.get('collect_person_data', {})
            first_name = person_data.get('first_name', '')
            last_name = person_data.get('last_name', '')
            
            property_data = context.get('collect_property_data', {})
            address = property_data.get('address', 'Property')
            
            return {
                "person_display_name": f"{first_name} {last_name}".strip() or "Person",
                "property_display_name": address
            }
        
        prepare_names = PythonOperator(
            task_id="prepare_names",
            python_callable=prepare_display_names
        )
        
        # Flow
        collect_person >> prepare_names >> create_person >> collect_property >> create_property >> create_ownership >> complete
    
    return dag