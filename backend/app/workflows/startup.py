"""
Startup initialization for the new DAG workflow system.
Registers available workflows and starts the executor.
"""
import logging
from ..services.workflow_service import workflow_service
from .examples.simple_workflow import get_available_workflows

logger = logging.getLogger(__name__)


async def initialize_workflow_system():
    """Initialize the workflow system with DAGs and start executor"""
    try:
        logger.info("Initializing DAG workflow system...")
        
        # Register available workflows
        workflows = get_available_workflows()
        
        for workflow_name, dag in workflows.items():
            try:
                await workflow_service.register_dag(dag, created_by="system")
                logger.info(f"Registered workflow: {workflow_name} ({dag.dag_id})")
            except Exception as e:
                logger.error(f"Failed to register workflow {workflow_name}: {str(e)}")
        
        # Start the executor
        await workflow_service.start_executor()
        logger.info("DAG executor started successfully")
        
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