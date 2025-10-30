import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from typing import Dict, Any, List

from .core.config import settings
from .core.database import connect_to_mongo, close_mongo_connection
from .api.api import api_router
from .workflows.startup import initialize_workflow_system, shutdown_workflow_system

DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:3001",  # AI services citizen portal
    "http://localhost:5173",
    "http://localhost:5174",  # AI services citizen portal dev
    "http://localhost:8080",
    "http://localhost:8000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
    "http://127.0.0.1:8080",
    "http://127.0.0.1:8000",
]


def load_cors_origins() -> List[str]:
    """Merge default origins with any configured in the environment."""
    raw_env = os.getenv("CORS_ORIGINS") or os.getenv("CORS_ORIGIN")
    if not raw_env:
        return DEFAULT_CORS_ORIGINS

    env_origins = [origin.strip() for origin in raw_env.split(",") if origin.strip()]
    if not env_origins:
        return DEFAULT_CORS_ORIGINS

    merged_origins = DEFAULT_CORS_ORIGINS.copy()
    for origin in env_origins:
        if origin not in merged_origins:
            merged_origins.append(origin)
    return merged_origins


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    redirect_slashes=False
)

# Set up CORS with defaults and optional environment overrides
app.add_middleware(
    CORSMiddleware,
    allow_origins=load_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "message": "Welcome to MuniStream API",
        "version": settings.VERSION,
        "docs": f"{settings.API_V1_STR}/docs"
    }


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION
    }



async def sync_plugin_dags():
    """
    Sync DAGs from plugins to the database and DAGBag.
    This ensures plugin DAGs are available through the API and can be executed.
    """
    from app.models.workflow import WorkflowDefinition
    from app.services.workflow_service import workflow_service
    
    print("\n📝 Syncing plugin DAGs to database...")
    
    synced_count = 0
    
    # The plugin_manager will load DAGs directly into the DAGBag
    # This function just ensures they're in the database for API access
    
    for dag_id, dag in workflow_service.dag_bag.dags.items():
        try:
            # Check if workflow already exists in database
            existing = await WorkflowDefinition.find_one(
                WorkflowDefinition.workflow_id == dag_id
            )
            
            if not existing:
                # Create new workflow definition for API access
                workflow_def = WorkflowDefinition(
                    workflow_id=dag_id,
                    name=dag.description or dag_id,
                    description=dag.description,
                    version="1.0.0",
                    status="active",
                    category="puente" if "puente" in dag.tags else "general",
                    tags=dag.tags,
                    created_by="system",
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                
                await workflow_def.save()
                print(f"  ✅ Synced DAG to database: {dag_id}")
                synced_count += 1
            
        except Exception as e:
            print(f"  ⚠️ Failed to sync DAG {dag_id}: {e}")
    
    print(f"✅ Synced {synced_count} DAGs to database")
    return synced_count


# Global plugin manager instance
plugin_manager = None

# Database events
@app.on_event("startup")
async def startup_event():
    await connect_to_mongo()
    
    # Load plugins before syncing workflows
    global plugin_manager
    try:
        from app.workflows.plugin_loader import WorkflowPluginManager
        from app.api.endpoints.plugins import plugin_manager as api_plugin_manager
        
        print("\n" + "="*60)
        print("🚀 STARTING MUNISTREAM BACKEND")
        print("="*60)
        
        print("\n📦 Loading workflow plugins...")
        plugin_manager = WorkflowPluginManager(config_file="plugins.yaml")
        plugin_manager.load_config()
        
        print(f"📋 Found {len(plugin_manager.plugins)} plugin configurations")
        
        workflows_loaded = plugin_manager.discover_and_load_workflows()
        
        print(f"\n✅ Successfully loaded {workflows_loaded} workflows from plugins")
        
        # Update the API plugin manager reference
        api_plugin_manager.plugins = plugin_manager.plugins
        
        # Sync plugin DAGs to database
        await sync_plugin_dags()
        
    except FileNotFoundError:
        print("⚠️ No plugins.yaml file found - skipping plugin loading")
    except Exception as e:
        print(f"⚠️ Warning: Error loading plugins: {e}")
        import traceback
        traceback.print_exc()
        # Don't fail startup if plugins can't load
    
    print("\n🔄 Initializing new DAG workflow system...")
    await initialize_workflow_system()
    
    print("\n" + "="*60)
    print("✅ MUNISTREAM BACKEND READY")
    print("="*60 + "\n")


@app.on_event("shutdown")
async def shutdown_event():
    await shutdown_workflow_system()
    await close_mongo_connection()


# Include API routes
app.include_router(api_router, prefix=settings.API_V1_STR)
