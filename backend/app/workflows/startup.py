"""
Startup initialization for the new DAG workflow system.
Registers available workflows, hooks, and starts the executor.
"""
import logging
from ..services.workflow_service import workflow_service
from .examples.simple_workflow import get_available_workflows
from .hook_registry import hook_registry

logger = logging.getLogger(__name__)


async def initialize_workflow_system():
    """Initialize the workflow system with DAGs and start executor"""
    try:
        # Use print for immediate visibility during debugging
        print("🔧 Initializing DAG workflow system...")
        logger.info("Initializing DAG workflow system...")
        
        # Register available workflows
        workflows = get_available_workflows()
        
        for workflow_name, dag in workflows.items():
            try:
                await workflow_service.register_dag(dag, created_by="system")
                print(f"✅ Registered workflow: {workflow_name} ({dag.dag_id})")
                logger.info(f"Registered workflow: {workflow_name} ({dag.dag_id})")
            except Exception as e:
                print(f"Failed to register workflow {workflow_name}: {str(e)}")
                logger.error(f"Failed to register workflow {workflow_name}: {str(e)}")
        
        # Set hook engine reference in the registry
        hook_registry.set_hook_engine(workflow_service.executor.event_manager.hook_engine)

        # Persist registered hooks to database
        print("🔗 Persisting workflow hooks...")
        await hook_registry.persist_hooks()
        print(f"✅ Persisted {len(hook_registry.get_registered_hooks())} workflow hooks")
        logger.info(f"Persisted {len(hook_registry.get_registered_hooks())} workflow hooks")

        # Start the executor
        print("🚀 Starting DAG executor...")
        await workflow_service.start_executor()
        print("✅ DAG executor started successfully")
        logger.info("DAG executor started successfully")

        print("✅ Workflow system initialization completed")
        logger.info("Workflow system initialization completed")
        
    except Exception as e:
        logger.error(f"Failed to initialize workflow system: {str(e)}")
        raise


async def shutdown_workflow_system():
    """Shutdown the workflow system gracefully"""
    try:
        logger.info("Shutting down workflow system...")
        await workflow_service.stop_executor()
        logger.info("Workflow system shutdown completed")
    except Exception as e:
        logger.error(f"Error during workflow system shutdown: {str(e)}")
        raise