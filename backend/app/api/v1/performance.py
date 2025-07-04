"""
Performance monitoring and analytics API endpoints.
"""

from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field

from ...workflows.executor import step_executor, StepExecutionContext
from ...workflows.base import BaseStep
from ...workflows.registry import step_registry

router = APIRouter()


class StepExecutionRequest(BaseModel):
    """Request model for manual step execution"""
    step_id: str
    inputs: Dict[str, Any] = Field(default_factory=dict)
    context: Dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False
    environment: str = "manual"


class StepValidationRequest(BaseModel):
    """Request model for step validation"""
    step_id: str
    inputs: Dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = True


class PerformanceMetricsResponse(BaseModel):
    """Response model for step performance metrics"""
    step_id: str
    total_executions: int
    successful_executions: int
    success_rate: float
    average_duration_ms: float
    min_duration_ms: float
    max_duration_ms: float
    average_queue_time_ms: float
    average_validation_time_ms: float
    common_errors: List[Dict[str, Any]]
    bottleneck_indicators: Dict[str, Any]


class WorkflowPerformanceResponse(BaseModel):
    """Response model for workflow performance analysis"""
    workflow_id: str
    total_instances: int
    completed_instances: int
    completion_rate: float
    average_completion_time_ms: float
    step_metrics: List[PerformanceMetricsResponse]
    bottleneck_analysis: Dict[str, Any]
    optimization_suggestions: List[str]


class BottleneckAnalysisResponse(BaseModel):
    """Response model for bottleneck analysis"""
    workflow_id: str
    critical_path_steps: List[str]
    slowest_steps: List[Dict[str, Any]]
    approval_bottlenecks: List[Dict[str, Any]]
    external_service_bottlenecks: List[Dict[str, Any]]
    queue_bottlenecks: List[Dict[str, Any]]
    recommendations: List[str]


@router.get("/steps/{step_id}/metrics", response_model=PerformanceMetricsResponse)
async def get_step_performance_metrics(step_id: str):
    """Get performance metrics for a specific step"""
    metrics = step_executor.get_step_performance_metrics(step_id)
    
    if "error" in metrics:
        raise HTTPException(status_code=404, detail=metrics["error"])
    
    return PerformanceMetricsResponse(**metrics)


@router.post("/steps/execute")
async def execute_step_manually(
    request: StepExecutionRequest,
    user_id: str = Query(..., description="User ID executing the step")
):
    """Manually execute a specific step with performance monitoring"""
    
    # Find the step using the step registry
    step = step_registry.get_step(request.step_id)
    if not step:
        raise HTTPException(status_code=404, detail=f"Step {request.step_id} not found")
    
    # Create execution context
    execution_context = StepExecutionContext(
        instance_id="manual_execution",
        user_id=user_id,
        execution_environment=request.environment
    )
    execution_context.mark_queued()
    
    try:
        if request.dry_run:
            # Validate only
            validation_result = await step_executor.validate_step_execution(
                step, request.inputs, dry_run=True
            )
            return {
                "step_id": request.step_id,
                "validation_result": validation_result.dict(),
                "dry_run": True
            }
        else:
            # Execute step
            result = await step_executor.execute_step(
                step, request.inputs, request.context, execution_context
            )
            return {
                "step_id": request.step_id,
                "execution_result": result.dict(),
                "dry_run": False
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Step execution failed: {str(e)}")


@router.post("/steps/validate")
async def validate_step(request: StepValidationRequest):
    """Validate a step without executing it"""
    
    # Find the step using the step registry
    step = step_registry.get_step(request.step_id)
    if not step:
        raise HTTPException(status_code=404, detail=f"Step {request.step_id} not found")
    
    try:
        validation_result = await step_executor.validate_step_execution(
            step, request.inputs, dry_run=request.dry_run
        )
        
        return {
            "step_id": request.step_id,
            "validation_result": validation_result.dict(),
            "timestamp": step_executor._get_current_timestamp()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Step validation failed: {str(e)}")


@router.get("/workflows/{workflow_id}/performance", response_model=WorkflowPerformanceResponse)
async def get_workflow_performance(
    workflow_id: str,
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)")
):
    """Get comprehensive performance analysis for a workflow"""
    
    try:
        # Get workflow
        workflow = step_registry.get_workflow(workflow_id)
        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")
        
        # Collect metrics for all steps in the workflow
        step_metrics = []
        total_optimization_suggestions = []
        
        for step in workflow.steps.values():
            metrics = step_executor.get_step_performance_metrics(step.step_id)
            if "error" not in metrics:
                step_metrics.append(PerformanceMetricsResponse(**metrics))
                if "bottleneck_indicators" in metrics and "suggested_optimizations" in metrics["bottleneck_indicators"]:
                    total_optimization_suggestions.extend(metrics["bottleneck_indicators"]["suggested_optimizations"])
        
        # Calculate workflow-level metrics
        total_executions = sum(m.total_executions for m in step_metrics)
        successful_executions = sum(m.successful_executions for m in step_metrics)
        
        completion_rate = (successful_executions / total_executions * 100) if total_executions > 0 else 0
        average_completion_time = sum(m.average_duration_ms for m in step_metrics) / len(step_metrics) if step_metrics else 0
        
        # Workflow-level bottleneck analysis
        bottleneck_analysis = {
            "total_steps": len(workflow.steps),
            "steps_with_data": len(step_metrics),
            "average_step_success_rate": sum(m.success_rate for m in step_metrics) / len(step_metrics) if step_metrics else 0,
            "slowest_step": max(step_metrics, key=lambda x: x.average_duration_ms).step_id if step_metrics else None,
            "most_error_prone_step": min(step_metrics, key=lambda x: x.success_rate).step_id if step_metrics else None
        }
        
        return WorkflowPerformanceResponse(
            workflow_id=workflow_id,
            total_instances=total_executions,
            completed_instances=successful_executions,
            completion_rate=completion_rate,
            average_completion_time_ms=average_completion_time,
            step_metrics=step_metrics,
            bottleneck_analysis=bottleneck_analysis,
            optimization_suggestions=list(set(total_optimization_suggestions))
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get workflow performance: {str(e)}")


@router.get("/workflows/{workflow_id}/bottlenecks", response_model=BottleneckAnalysisResponse)
async def analyze_workflow_bottlenecks(
    workflow_id: str,
    threshold_ms: int = Query(5000, description="Threshold in milliseconds for slow steps"),
    min_executions: int = Query(10, description="Minimum executions required for analysis")
):
    """Analyze bottlenecks in a workflow"""
    
    try:
        workflow = step_registry.get_workflow(workflow_id)
        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")
        
        # Analyze each step for bottlenecks
        slowest_steps = []
        approval_bottlenecks = []
        external_service_bottlenecks = []
        queue_bottlenecks = []
        recommendations = []
        
        for step in workflow.steps.values():
            metrics = step_executor.get_step_performance_metrics(step.step_id)
            
            if "error" not in metrics and metrics.get("total_executions", 0) >= min_executions:
                avg_duration = metrics.get("average_duration_ms", 0)
                avg_queue_time = metrics.get("average_queue_time_ms", 0)
                bottleneck_indicators = metrics.get("bottleneck_indicators", {})
                
                # Slow execution steps
                if avg_duration > threshold_ms:
                    slowest_steps.append({
                        "step_id": step.step_id,
                        "step_name": step.name,
                        "average_duration_ms": avg_duration,
                        "executions": metrics.get("total_executions", 0)
                    })
                
                # Queue bottlenecks
                if avg_queue_time > threshold_ms / 2:  # Half the threshold for queue time
                    queue_bottlenecks.append({
                        "step_id": step.step_id,
                        "step_name": step.name,
                        "average_queue_time_ms": avg_queue_time,
                        "executions": metrics.get("total_executions", 0)
                    })
                
                # Approval bottlenecks
                approval_percentage = bottleneck_indicators.get("approval_bottleneck_percentage", 0)
                if approval_percentage > 30:  # More than 30% waiting for approval
                    approval_bottlenecks.append({
                        "step_id": step.step_id,
                        "step_name": step.name,
                        "approval_bottleneck_percentage": approval_percentage,
                        "executions": metrics.get("total_executions", 0)
                    })
                
                # External service bottlenecks
                external_percentage = bottleneck_indicators.get("external_service_bottleneck_percentage", 0)
                if external_percentage > 20:  # More than 20% waiting for external services
                    external_service_bottlenecks.append({
                        "step_id": step.step_id,
                        "step_name": step.name,
                        "external_service_bottleneck_percentage": external_percentage,
                        "executions": metrics.get("total_executions", 0)
                    })
                
                # Collect recommendations
                step_recommendations = bottleneck_indicators.get("suggested_optimizations", [])
                recommendations.extend([f"{step.name}: {rec}" for rec in step_recommendations])
        
        # Sort by impact (duration * executions for slowest steps)
        slowest_steps.sort(key=lambda x: x["average_duration_ms"] * x["executions"], reverse=True)
        
        # Determine critical path (simplified - in reality you'd use graph analysis)
        critical_path_steps = [step["step_id"] for step in slowest_steps[:3]]
        
        return BottleneckAnalysisResponse(
            workflow_id=workflow_id,
            critical_path_steps=critical_path_steps,
            slowest_steps=slowest_steps,
            approval_bottlenecks=approval_bottlenecks,
            external_service_bottlenecks=external_service_bottlenecks,
            queue_bottlenecks=queue_bottlenecks,
            recommendations=list(set(recommendations))
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to analyze bottlenecks: {str(e)}")


@router.get("/workflows/{workflow_id}/timing-analysis")
async def get_workflow_timing_analysis(
    workflow_id: str,
    instance_id: Optional[str] = Query(None, description="Specific instance to analyze")
):
    """Get detailed timing analysis showing step-to-step execution times"""
    
    try:
        # This would analyze the timing between steps to show where citizens spend most time
        # In a real implementation, you'd query your database for instance execution data
        
        timing_analysis = {
            "workflow_id": workflow_id,
            "instance_id": instance_id,
            "step_transitions": [
                {
                    "from_step": "validate_identity",
                    "to_step": "identity_check",
                    "average_transition_time_ms": 150,
                    "citizen_wait_time_ms": 0  # Automated transition
                },
                {
                    "from_step": "identity_check",
                    "to_step": "check_duplicates",
                    "average_transition_time_ms": 200,
                    "citizen_wait_time_ms": 0
                },
                {
                    "from_step": "adult_approval",
                    "to_step": "process_approval",
                    "average_transition_time_ms": 86400000,  # 24 hours in ms
                    "citizen_wait_time_ms": 86400000  # Citizens wait for approval
                }
            ],
            "bottleneck_transitions": [
                {
                    "transition": "adult_approval -> process_approval",
                    "average_wait_time_ms": 86400000,
                    "reason": "Manual approval process",
                    "suggestion": "Consider automated approval for low-risk cases"
                }
            ],
            "total_average_completion_time_ms": 86405000,
            "citizen_active_time_ms": 5000,  # Time citizen is actively involved
            "citizen_waiting_time_ms": 86400000  # Time citizen waits for system/approvers
        }
        
        return timing_analysis
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get timing analysis: {str(e)}")


@router.get("/workflows")
async def list_workflows():
    """List all available workflows"""
    return {
        "workflows": step_registry.list_workflows()
    }


@router.get("/steps")
async def list_steps(
    workflow_id: Optional[str] = Query(None, description="Filter steps by workflow")
):
    """List all available steps, optionally filtered by workflow"""
    return {
        "steps": step_registry.list_steps(workflow_id)
    }


@router.get("/stats")
async def get_workflow_stats():
    """Get overall workflow statistics"""
    try:
        # Mock data for now - in production this would come from database
        stats = {
            "total_instances": 42,
            "active_instances": 8,
            "completed_today": 15,
            "avg_completion_time_hours": 2.5,
            "success_rate": 0.92,
            "by_workflow": {
                "citizen_registration_v1": 15,
                "citizen_registration_with_docs_v1": 12,
                "building_permit_v1": 15
            },
            "by_status": {
                "running": 8,
                "completed": 28,
                "failed": 4,
                "paused": 2
            }
        }
        
        return stats
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")


@router.get("/health")
async def performance_service_health():
    """Health check for performance monitoring service"""
    return {
        "status": "healthy",
        "service": "performance_monitoring",
        "executor_metrics_count": len(step_executor.execution_metrics),
        "total_tracked_executions": sum(
            len(executions) for executions in step_executor.execution_metrics.values()
        )
    }