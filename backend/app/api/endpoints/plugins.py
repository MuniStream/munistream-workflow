"""
API endpoints for managing workflow plugins.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, HttpUrl

from ...workflows.plugin_loader import WorkflowPluginManager
from ...services.auth_service import get_current_user, require_admin

router = APIRouter()

# Global plugin manager instance
plugin_manager = WorkflowPluginManager()


class PluginAdd(BaseModel):
    """Request model for adding a new plugin"""
    name: str
    repo_url: HttpUrl
    
    
class PluginInfo(BaseModel):
    """Plugin information response"""
    name: str
    version: str
    repo_url: str
    workflow_count: int
    enabled: bool


class PluginLoadResponse(BaseModel):
    """Response for plugin loading operation"""
    success: bool
    workflows_loaded: int
    message: str


@router.get("/", response_model=List[PluginInfo])
async def list_plugins(current_user = Depends(require_admin)):
    """List all configured workflow plugins"""
    return plugin_manager.list_plugins()


@router.post("/add", response_model=PluginInfo)
async def add_plugin(
    plugin_data: PluginAdd,
    current_user = Depends(require_admin)
):
    """Add a new workflow plugin from a git repository"""
    success = plugin_manager.add_plugin_from_url(
        repo_url=str(plugin_data.repo_url),
        name=plugin_data.name
    )
    
    if not success:
        raise HTTPException(status_code=400, detail="Failed to add plugin")
    
    # Find the newly added plugin
    plugins = plugin_manager.list_plugins()
    new_plugin = next(
        (p for p in plugins if p["name"] == plugin_data.name),
        None
    )
    
    if not new_plugin:
        raise HTTPException(status_code=500, detail="Plugin added but not found")
    
    return PluginInfo(**new_plugin)


@router.post("/reload", response_model=PluginLoadResponse)
async def reload_plugins(current_user = Depends(require_admin)):
    """Reload all plugins and their workflows"""
    try:
        # Clear existing plugins
        plugin_manager.plugins = []
        
        # Load configuration
        plugin_manager.load_config()
        
        # Clear registry to avoid duplicates
        from ...workflows.registry import step_registry
        step_registry._workflows = {}
        
        # Discover and load workflows
        workflows_loaded = plugin_manager.discover_and_load_workflows()
        
        # Sync to database
        from ...main import sync_programmatic_workflows
        await sync_programmatic_workflows()
        
        return PluginLoadResponse(
            success=True,
            workflows_loaded=workflows_loaded,
            message=f"Successfully loaded {workflows_loaded} workflows from {len(plugin_manager.plugins)} plugins"
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return PluginLoadResponse(
            success=False,
            workflows_loaded=0,
            message=f"Error loading plugins: {str(e)}"
        )