#!/usr/bin/env python3
"""
Test script to demonstrate performance monitoring capabilities.
"""

import asyncio
import json
from datetime import datetime

from app.workflows.registry import step_registry
from app.workflows.executor import step_executor, StepExecutionContext


async def demonstrate_performance_monitoring():
    """Demonstrate the performance monitoring system"""
    
    print("🚀 CivicStream Performance Monitoring Demo")
    print("=" * 50)
    
    # List available workflows
    print("\n📋 Available Workflows:")
    workflows = step_registry.list_workflows()
    for workflow in workflows:
        print(f"  - {workflow['workflow_id']}: {workflow['name']} ({workflow['step_count']} steps)")
    
    if not workflows:
        print("  No workflows registered")
        return
    
    # Get the first workflow for demonstration
    workflow_id = workflows[0]['workflow_id']
    workflow = step_registry.get_workflow(workflow_id)
    
    print(f"\n🔍 Analyzing workflow: {workflow.name}")
    
    # List steps in the workflow
    print(f"\n📊 Steps in {workflow.name}:")
    steps = step_registry.list_steps(workflow_id)
    for step in steps[:5]:  # Show first 5 steps
        print(f"  - {step['step_id']}: {step['name']} ({step['type']})")
    
    if len(steps) > 5:
        print(f"  ... and {len(steps) - 5} more steps")
    
    # Simulate some step executions for demonstration
    print(f"\n⚡ Simulating step executions for performance analysis...")
    
    # Get a few steps to simulate
    demo_steps = steps[:3]  # First 3 steps
    
    for step_info in demo_steps:
        step = step_registry.get_step(step_info['step_id'])
        if step:
            print(f"\n  🔄 Executing step: {step.name}")
            
            # Create execution context
            execution_context = StepExecutionContext(
                instance_id="demo_instance",
                user_id="demo_user",
                execution_environment="demo"
            )
            execution_context.mark_queued()
            
            # Simulate inputs based on step requirements
            demo_inputs = {}
            for required_input in step.required_inputs:
                if required_input == "email":
                    demo_inputs[required_input] = "demo@example.com"
                elif required_input == "first_name":
                    demo_inputs[required_input] = "John"
                elif required_input == "last_name":
                    demo_inputs[required_input] = "Doe"
                elif required_input == "birth_date":
                    demo_inputs[required_input] = "1990-01-01"
                else:
                    demo_inputs[required_input] = f"demo_value_{required_input}"
            
            try:
                # Execute the step
                result = await step_executor.execute_step(
                    step, demo_inputs, {}, execution_context
                )
                
                print(f"    ✅ Status: {result.status}")
                print(f"    ⏱️  Duration: {result.execution_duration_ms}ms")
                print(f"    🔧 Queue Time: {result.queue_time_ms}ms")
                
                if result.error:
                    print(f"    ❌ Error: {result.error}")
                
            except Exception as e:
                print(f"    ❌ Execution failed: {str(e)}")
    
    # Demonstrate performance metrics
    print(f"\n📈 Performance Metrics Analysis:")
    
    for step_info in demo_steps:
        step_id = step_info['step_id']
        metrics = step_executor.get_step_performance_metrics(step_id)
        
        if "error" not in metrics:
            print(f"\n  📊 Step: {step_info['name']}")
            print(f"    Total Executions: {metrics.get('total_executions', 0)}")
            print(f"    Success Rate: {metrics.get('success_rate', 0):.1f}%")
            print(f"    Avg Duration: {metrics.get('average_duration_ms', 0):.1f}ms")
            print(f"    Avg Queue Time: {metrics.get('average_queue_time_ms', 0):.1f}ms")
            
            bottlenecks = metrics.get('bottleneck_indicators', {})
            if bottlenecks.get('suggested_optimizations'):
                print(f"    💡 Suggestions: {', '.join(bottlenecks['suggested_optimizations'])}")
        else:
            print(f"\n  📊 Step: {step_info['name']} - {metrics['error']}")
    
    print(f"\n🏁 Demo completed!")
    print(f"\n💡 Next steps:")
    print("  - Start the FastAPI server: uvicorn app.main:app --reload")
    print("  - Access performance API at: http://localhost:8000/api/v1/performance/")
    print("  - View available endpoints:")
    print("    • GET /performance/workflows - List workflows")
    print("    • GET /performance/steps - List steps")
    print("    • GET /performance/steps/{step_id}/metrics - Step performance")
    print("    • POST /performance/steps/execute - Manual step execution")
    print("    • GET /performance/workflows/{workflow_id}/bottlenecks - Bottleneck analysis")


if __name__ == "__main__":
    asyncio.run(demonstrate_performance_monitoring())