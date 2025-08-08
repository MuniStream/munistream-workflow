"""
Test script to demonstrate the complete integration of the new DAG system.
Shows how multiple users can run the same workflow concurrently.
"""
import asyncio
import logging
from datetime import datetime

from ..dag import DAGBag
from ..executor import DAGExecutor
from .simple_workflow import create_simple_workflow

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_single_instance():
    """Test a single workflow instance"""
    print("=== Testing Single Instance ===")
    
    # Create workflow definition
    dag = create_simple_workflow()
    
    # Create DAG bag and add our workflow
    dag_bag = DAGBag()
    dag_bag.add_dag(dag)
    
    # Create executor
    executor = DAGExecutor(max_concurrent_instances=5)
    await executor.start()
    
    try:
        # Create instance for a citizen
        instance = dag_bag.create_instance(
            dag_id="simple_certificate_workflow",
            user_id="citizen_001",
            initial_data={"request_type": "certificate", "priority": "normal"}
        )
        
        print(f"Created instance: {instance.instance_id}")
        print(f"Initial context: {list(instance.context.keys())}")
        
        # Submit to executor
        executor.submit_instance(instance)
        
        # Wait a bit for execution to start
        await asyncio.sleep(2)
        
        print(f"Instance status: {instance.status}")
        print(f"Progress: {instance.get_progress_percentage():.1f}%")
        
        # Simulate user providing input to the first task
        collect_task = dag.get_task_by_id("collect_user_data")
        if collect_task and collect_task.state.status == "waiting_input":
            print("Simulating user input...")
            collect_task.receive_input({
                "nombre": "Juan Pérez",
                "email": "juan@example.com", 
                "telefono": "555-1234"
            })
            
            # Resume execution
            executor.resume_instance(instance.instance_id)
            await asyncio.sleep(1)
        
        print(f"After user input - Status: {instance.status}")
        print(f"Progress: {instance.get_progress_percentage():.1f}%")
        print(f"Context keys: {list(instance.context.keys())}")
        
    finally:
        await executor.stop()


async def test_concurrent_instances():
    """Test multiple concurrent instances of the same workflow"""
    print("\n=== Testing Concurrent Instances ===")
    
    # Create workflow definition
    dag = create_simple_workflow()
    
    # Create DAG bag and executor
    dag_bag = DAGBag()
    dag_bag.add_dag(dag)
    executor = DAGExecutor(max_concurrent_instances=3)
    
    await executor.start()
    
    try:
        # Create multiple instances for different citizens
        instances = []
        users = ["citizen_001", "citizen_002", "citizen_003", "citizen_004"]
        
        for user_id in users:
            instance = dag_bag.create_instance(
                dag_id="simple_certificate_workflow",
                user_id=user_id,
                initial_data={
                    "request_type": "certificate",
                    "user_type": "regular",
                    "submitted_at": datetime.now().isoformat()
                }
            )
            instances.append(instance)
            executor.submit_instance(instance)
            print(f"Submitted instance for {user_id}: {instance.instance_id}")
        
        # Wait for initial processing
        await asyncio.sleep(3)
        
        # Check status of all instances
        print("\nInstance Status:")
        for i, instance in enumerate(instances):
            print(f"  {users[i]}: {instance.status} - {instance.get_progress_percentage():.1f}%")
            print(f"    Current task: {instance.current_task}")
            print(f"    Completed tasks: {len(instance.completed_tasks)}")
        
        # Simulate user input for some instances
        print("\nSimulating user inputs...")
        user_data = [
            {"nombre": "Ana García", "email": "ana@example.com"},
            {"nombre": "Carlos López", "email": "carlos@example.com"},
            {"nombre": "María Rodríguez", "email": "maria@example.com"}
        ]
        
        for i, instance in enumerate(instances[:3]):  # Only first 3
            collect_task = dag.get_task_by_id("collect_user_data")
            if collect_task:
                # Each instance has its own copy of the task, so we need to find the right one
                # In a real implementation, we'd track task instances per DAG instance
                print(f"  Providing input for {users[i]}")
                # This is simplified - in reality we'd need to track task states per instance
        
        print("\nFinal Status:")
        stats = executor.get_stats()
        print(f"  Running instances: {stats['running_instances']}")
        print(f"  Queued instances: {stats['queued_instances']}")
        print(f"  Total executed: {stats['total_executed']}")
        
    finally:
        await executor.stop()


async def test_workflow_definition():
    """Test the workflow definition and validation"""
    print("\n=== Testing Workflow Definition ===")
    
    # Create and validate workflow
    dag = create_simple_workflow()
    
    print(f"DAG ID: {dag.dag_id}")
    print(f"Description: {dag.description}")
    print(f"Tasks: {len(dag.tasks)}")
    print(f"Status: {dag.status}")
    
    # Show task structure
    print("\nTask Structure:")
    for task_id, task in dag.tasks.items():
        print(f"  {task_id} ({task.__class__.__name__})")
        print(f"    Upstream: {[t.task_id for t in task.upstream_tasks]}")
        print(f"    Downstream: {[t.task_id for t in task.downstream_tasks]}")
    
    # Show execution order
    print(f"\nExecution Order: {dag.get_execution_order()}")
    
    # Generate Mermaid diagram
    print(f"\nMermaid Diagram:")
    print(dag.to_mermaid())


async def main():
    """Run all tests"""
    print("Testing New DAG System with Self-Contained Operators")
    print("=" * 60)
    
    # Test workflow definition
    await test_workflow_definition()
    
    # Test single instance
    await test_single_instance()
    
    # Test concurrent instances  
    await test_concurrent_instances()
    
    print("\n" + "=" * 60)
    print("All tests completed!")


if __name__ == "__main__":
    asyncio.run(main())