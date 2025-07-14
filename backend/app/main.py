from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .core.config import settings
from .core.database import connect_to_mongo, close_mongo_connection
from .api.api import api_router
from .services.workflow_service import WorkflowService
from .workflows.registry import step_registry

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

# Set up CORS - temporary hardcoded for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "http://localhost:8080", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "message": "Welcome to CivicStream API",
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


@app.get("/debug/cors")
async def debug_cors():
    return {
        "cors_origins": settings.BACKEND_CORS_ORIGINS,
        "cors_origins_type": type(settings.BACKEND_CORS_ORIGINS).__name__
    }


async def sync_programmatic_workflows():
    """Sync programmatic workflows from registry to database"""
    try:
        # Get all workflows from the registry
        registry_workflows = step_registry.list_workflows()
        
        for workflow_info in registry_workflows:
            workflow_id = workflow_info["workflow_id"]
            workflow = step_registry.get_workflow(workflow_id)
            
            if workflow:
                print(f"Syncing workflow: {workflow_id}")
                
                # Check if workflow definition exists in database
                existing_def = await WorkflowService.get_workflow_definition(workflow_id)
                
                if not existing_def:
                    # Create workflow definition
                    await WorkflowService.create_workflow_definition(
                        workflow_id=workflow.workflow_id,
                        name=workflow.name,
                        description=workflow.description,
                        version="1.0.0"
                    )
                    print(f"Created workflow definition: {workflow_id}")
                
                # Sync workflow steps to database
                await WorkflowService.save_workflow_steps(workflow_id, workflow)
                print(f"Synced {len(workflow.steps)} steps for workflow: {workflow_id}")
                
                # Update start step if defined
                if workflow.start_step:
                    current_def = await WorkflowService.get_workflow_definition(workflow_id)
                    if current_def:
                        await WorkflowService.update_workflow_definition(
                            workflow_id, 
                            {"start_step_id": workflow.start_step.step_id}
                        )
        
        print("Workflow synchronization completed successfully")
        
    except Exception as e:
        print(f"Error syncing workflows: {e}")
        # Don't raise to prevent app startup failure


# Database events
@app.on_event("startup")
async def startup_event():
    await connect_to_mongo()
    
    # Load plugins before syncing workflows
    try:
        from app.workflows.plugin_loader import WorkflowPluginManager
        print("Loading workflow plugins...")
        plugin_manager = WorkflowPluginManager(config_file="plugins.yaml")
        plugin_manager.load_config()
        workflows_loaded = plugin_manager.discover_and_load_workflows()
        print(f"Loaded {workflows_loaded} workflows from plugins")
    except Exception as e:
        print(f"Warning: Error loading plugins: {e}")
        # Don't fail startup if plugins can't load
    
    await sync_programmatic_workflows()


@app.on_event("shutdown")
async def shutdown_event():
    await close_mongo_connection()


# Include API routes
app.include_router(api_router, prefix=settings.API_V1_STR)