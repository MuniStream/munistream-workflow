"""
Script to initialize basic entity types in the database.
"""
import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent.parent))

from app.core.database import connect_to_mongo, close_mongo_connection
from app.services.entity_service import EntityService


async def init_entity_types():
    """Initialize basic entity types"""
    await connect_to_mongo()
    
    try:
        # Create person entity type
        print("Creating entity types...")
        
        person_type = await EntityService.create_entity_type(
            type_id="person",
            name="Persona",
            alias="Persona Física",
            description="Persona física o individuo",
            icon="person",
            color="#4CAF50"
        )
        print(f"✅ Created entity type: {person_type.type_id}")
    except Exception as e:
        print(f"Person type may already exist: {e}")
    
    try:
        # Create property entity type
        property_type = await EntityService.create_entity_type(
            type_id="property",
            name="Propiedad",
            alias="Bien Inmueble",
            description="Propiedad o bien inmueble",
            icon="home",
            color="#2196F3"
        )
        print(f"✅ Created entity type: {property_type.type_id}")
    except Exception as e:
        print(f"Property type may already exist: {e}")
    
    try:
        # Create vehicle entity type
        vehicle_type = await EntityService.create_entity_type(
            type_id="vehicle",
            name="Vehículo",
            alias="Automotor",
            description="Vehículo automotor",
            icon="car",
            color="#FF9800"
        )
        print(f"✅ Created entity type: {vehicle_type.type_id}")
    except Exception as e:
        print(f"Vehicle type may already exist: {e}")
    
    try:
        # Create company entity type
        company_type = await EntityService.create_entity_type(
            type_id="company",
            name="Empresa",
            alias="Persona Moral",
            description="Empresa o persona moral",
            icon="business",
            color="#9C27B0"
        )
        print(f"✅ Created entity type: {company_type.type_id}")
    except Exception as e:
        print(f"Company type may already exist: {e}")
    
    try:
        # Create document entity type
        document_type = await EntityService.create_entity_type(
            type_id="document",
            name="Documento",
            alias="Documento Oficial",
            description="Documento oficial o certificado",
            icon="document",
            color="#FFC107"
        )
        print(f"✅ Created entity type: {document_type.type_id}")
    except Exception as e:
        print(f"Document type may already exist: {e}")
    
    # List all entity types
    print("\n📋 Available entity types:")
    types = await EntityService.list_entity_types()
    for et in types:
        print(f"  - {et.type_id}: {et.name}")
    
    await close_mongo_connection()
    print("\n✅ Entity types initialization complete!")


if __name__ == "__main__":
    asyncio.run(init_entity_types())