"""
Performance monitoring and analytics API endpoints.
"""

from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field

from ...services.workflow_service import workflow_service

router = APIRouter()


def _median(values: List[float]) -> Optional[float]:
    """Median of a list of numbers (None when empty)."""
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


def _percentile(values: List[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile of a list of numbers (None when empty)."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = int(round((pct / 100.0) * (len(s) - 1)))
    return s[k]


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


class EfficiencyKPIs(BaseModel):
    """Instance-level efficiency indicators for a workflow"""
    total_instances: int
    active_instances: int
    completed_instances: int
    failed_instances: int
    completion_rate: float
    avg_duration_seconds: Optional[float] = None
    median_duration_seconds: Optional[float] = None
    overall_efficiency: float


class StepBottleneck(BaseModel):
    """Per-step dwell-time bottleneck indicator"""
    step_id: str
    step_name: str
    avg_duration_seconds: Optional[float] = None
    p95_duration_seconds: Optional[float] = None
    execution_count: int
    currently_stuck_count: int
    severity: str


class StatusCount(BaseModel):
    """Instance count for a status value"""
    status: str
    count: int


class VolumePoint(BaseModel):
    """Daily started/completed instance counts"""
    date: str
    started: int
    completed: int


class WorkflowAnalyticsResponse(BaseModel):
    """Consolidated analytics for a single workflow"""
    workflow_id: str
    generated_at: datetime
    kpis: EfficiencyKPIs
    bottlenecks: List[StepBottleneck]
    status_distribution: List[StatusCount]
    volume_over_time: List[VolumePoint]


@router.get("/workflows/{workflow_id}/analytics", response_model=WorkflowAnalyticsResponse)
async def get_workflow_analytics(
    workflow_id: str,
    days: int = Query(30, description="Time window in days for the volume series"),
    stuck_threshold_hours: int = Query(24, description="Hours idle before an active instance counts as stuck"),
):
    """Consolidated analytics for a workflow: efficiency KPIs, per-step
    bottlenecks, status distribution and volume over time. Computed from real
    WorkflowInstance and StepExecution data via native MongoDB aggregation."""
    from ...models.workflow import WorkflowInstance, StepExecution, WorkflowStep

    dag = await workflow_service.get_dag(workflow_id)
    if not dag:
        raise HTTPException(status_code=404, detail="Workflow not found")

    try:
        now = datetime.utcnow()
        active_statuses = ["pending", "running", "paused"]

        # ---- (a) Efficiency KPIs -------------------------------------------
        kpi_pipeline = [
            {"$match": {"workflow_id": workflow_id}},
            {"$addFields": {"_dur": {"$ifNull": [
                "$duration_seconds",
                {"$cond": [
                    {"$and": [
                        {"$ne": ["$completed_at", None]},
                        {"$ne": ["$started_at", None]},
                    ]},
                    {"$divide": [{"$subtract": ["$completed_at", "$started_at"]}, 1000]},
                    None,
                ]},
            ]}}},
            {"$group": {
                "_id": None,
                "total": {"$sum": 1},
                "completed": {"$sum": {"$cond": [{"$eq": ["$status", "completed"]}, 1, 0]}},
                "failed": {"$sum": {"$cond": [{"$eq": ["$status", "failed"]}, 1, 0]}},
                "active": {"$sum": {"$cond": [{"$in": ["$status", active_statuses]}, 1, 0]}},
                "avg_duration": {"$avg": "$_dur"},
                "durations": {"$push": "$_dur"},
            }},
        ]
        kpi_rows = await WorkflowInstance.aggregate(kpi_pipeline).to_list()
        if kpi_rows:
            row = kpi_rows[0]
            total = row.get("total", 0)
            completed = row.get("completed", 0)
            failed = row.get("failed", 0)
            active = row.get("active", 0)
            durations = [d for d in row.get("durations", []) if d is not None]
            median_duration = _median(durations)
            avg_duration = row.get("avg_duration")
        else:
            total = completed = failed = active = 0
            median_duration = None
            avg_duration = None

        completion_rate = (completed / total * 100) if total else 0.0
        efficiency_denom = completed + failed
        overall_efficiency = (completed / efficiency_denom * 100) if efficiency_denom else 0.0

        kpis = EfficiencyKPIs(
            total_instances=total,
            active_instances=active,
            completed_instances=completed,
            failed_instances=failed,
            completion_rate=round(completion_rate, 2),
            avg_duration_seconds=round(avg_duration, 2) if avg_duration is not None else None,
            median_duration_seconds=round(median_duration, 2) if median_duration is not None else None,
            overall_efficiency=round(overall_efficiency, 2),
        )

        # ---- (b) Per-step bottlenecks --------------------------------------
        step_pipeline = [
            {"$match": {"workflow_id": workflow_id, "duration_seconds": {"$ne": None}}},
            {"$group": {
                "_id": "$step_id",
                "execution_count": {"$sum": 1},
                "avg_duration": {"$avg": "$duration_seconds"},
                "durations": {"$push": "$duration_seconds"},
            }},
        ]
        step_rows = await StepExecution.aggregate(step_pipeline).to_list()

        stuck_pipeline = [
            {"$match": {
                "workflow_id": workflow_id,
                "status": {"$in": active_statuses},
                "current_step": {"$ne": None},
                "updated_at": {"$lt": now - timedelta(hours=stuck_threshold_hours)},
            }},
            {"$group": {"_id": "$current_step", "count": {"$sum": 1}}},
        ]
        stuck_rows = await WorkflowInstance.aggregate(stuck_pipeline).to_list()
        stuck_by_step = {r["_id"]: r["count"] for r in stuck_rows if r.get("_id")}

        step_defs = await WorkflowStep.find(WorkflowStep.workflow_id == workflow_id).to_list()
        name_by_step = {s.step_id: s.name for s in step_defs}

        metrics_by_step: Dict[str, Dict[str, Any]] = {}
        for r in step_rows:
            sid = r["_id"]
            durs = [d for d in r.get("durations", []) if d is not None]
            metrics_by_step[sid] = {
                "execution_count": r.get("execution_count", 0),
                "avg_duration": r.get("avg_duration"),
                "p95": _percentile(durs, 95),
            }

        # Union of steps with executions and steps holding stuck instances.
        all_step_ids = set(metrics_by_step) | set(stuck_by_step)

        # Severity by dwell ranking; escalate any step with stuck instances.
        ranked = sorted(
            [sid for sid in all_step_ids if metrics_by_step.get(sid, {}).get("avg_duration") is not None],
            key=lambda s: metrics_by_step[s]["avg_duration"],
            reverse=True,
        )
        severity_by_step: Dict[str, str] = {}
        n = len(ranked)
        for idx, sid in enumerate(ranked):
            if idx == 0:
                severity_by_step[sid] = "high"
            elif idx < max(1, n // 4):
                severity_by_step[sid] = "medium"
            else:
                severity_by_step[sid] = "low"

        bottlenecks: List[StepBottleneck] = []
        for sid in all_step_ids:
            m = metrics_by_step.get(sid, {})
            stuck = stuck_by_step.get(sid, 0)
            severity = severity_by_step.get(sid, "none")
            if stuck > 0 and severity != "high":
                severity = "high"
            avg = m.get("avg_duration")
            p95 = m.get("p95")
            bottlenecks.append(StepBottleneck(
                step_id=sid,
                step_name=name_by_step.get(sid) or sid.replace("_", " ").title(),
                avg_duration_seconds=round(avg, 2) if avg is not None else None,
                p95_duration_seconds=round(p95, 2) if p95 is not None else None,
                execution_count=m.get("execution_count", 0),
                currently_stuck_count=stuck,
                severity=severity,
            ))
        bottlenecks.sort(
            key=lambda b: (b.avg_duration_seconds or 0) * max(b.execution_count, 1),
            reverse=True,
        )

        # ---- (c) Status distribution ---------------------------------------
        status_pipeline = [
            {"$match": {"workflow_id": workflow_id}},
            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]
        status_rows = await WorkflowInstance.aggregate(status_pipeline).to_list()
        status_distribution = [
            StatusCount(status=r["_id"] or "unknown", count=r["count"]) for r in status_rows
        ]

        # ---- (d) Volume over time ------------------------------------------
        window_start = now - timedelta(days=days)
        started_pipeline = [
            {"$match": {"workflow_id": workflow_id, "started_at": {"$gte": window_start}}},
            {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$started_at"}}, "count": {"$sum": 1}}},
        ]
        completed_pipeline = [
            {"$match": {"workflow_id": workflow_id, "completed_at": {"$gte": window_start}}},
            {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$completed_at"}}, "count": {"$sum": 1}}},
        ]
        started_rows = await WorkflowInstance.aggregate(started_pipeline).to_list()
        completed_rows = await WorkflowInstance.aggregate(completed_pipeline).to_list()
        started_by_day = {r["_id"]: r["count"] for r in started_rows if r.get("_id")}
        completed_by_day = {r["_id"]: r["count"] for r in completed_rows if r.get("_id")}

        volume_over_time: List[VolumePoint] = []
        day = window_start.date()
        end_day = now.date()
        while day <= end_day:
            key = day.strftime("%Y-%m-%d")
            volume_over_time.append(VolumePoint(
                date=key,
                started=started_by_day.get(key, 0),
                completed=completed_by_day.get(key, 0),
            ))
            day += timedelta(days=1)

        return WorkflowAnalyticsResponse(
            workflow_id=workflow_id,
            generated_at=now,
            kpis=kpis,
            bottlenecks=bottlenecks,
            status_distribution=status_distribution,
            volume_over_time=volume_over_time,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to compute workflow analytics: {str(e)}")


@router.get("/steps/{step_id}/metrics", response_model=PerformanceMetricsResponse)
async def get_step_performance_metrics(step_id: str):
    """Get performance metrics for a specific step using new DAG system"""
    from ...models.workflow import StepExecution
    
    # Get all executions for this step
    executions = await StepExecution.find(StepExecution.step_id == step_id).to_list()
    
    if not executions:
        raise HTTPException(status_code=404, detail=f"No executions found for step {step_id}")
    
    # Calculate metrics
    total_executions = len(executions)
    successful_executions = len([e for e in executions if e.status == "completed"])
    success_rate = (successful_executions / total_executions) * 100 if total_executions > 0 else 0
    
    # Duration metrics (only for completed executions)
    completed_executions = [e for e in executions if e.duration_seconds is not None]
    durations_ms = [e.duration_seconds * 1000 for e in completed_executions]
    
    avg_duration = sum(durations_ms) / len(durations_ms) if durations_ms else 0
    min_duration = min(durations_ms) if durations_ms else 0
    max_duration = max(durations_ms) if durations_ms else 0
    
    # Common errors
    error_executions = [e for e in executions if e.status == "failed" and e.error_message]
    common_errors = []
    if error_executions:
        error_counts = {}
        for e in error_executions:
            error_msg = e.error_message[:100]  # Truncate for grouping
            error_counts[error_msg] = error_counts.get(error_msg, 0) + 1
        
        common_errors = [
            {"error": error, "count": count, "percentage": (count/len(error_executions))*100}
            for error, count in sorted(error_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        ]
    
    return PerformanceMetricsResponse(
        step_id=step_id,
        total_executions=total_executions,
        successful_executions=successful_executions,
        success_rate=round(success_rate, 2),
        average_duration_ms=round(avg_duration, 2),
        min_duration_ms=round(min_duration, 2),
        max_duration_ms=round(max_duration, 2),
        average_queue_time_ms=0.0,  # TODO: Implement queue time tracking
        average_validation_time_ms=0.0,  # TODO: Implement validation time tracking
        common_errors=common_errors,
        bottleneck_indicators={"slow_executions": len([d for d in durations_ms if d > avg_duration * 2])}
    )


@router.post("/steps/execute")
async def execute_step_manually(
    request: StepExecutionRequest,
    user_id: str = Query(..., description="User ID executing the step")
):
    """Manually execute a specific step using new DAG system"""
    from datetime import datetime
    import uuid
    from ...models.workflow import StepExecution
    
    # Create a temporary DAG instance for manual execution
    dag_instance = await workflow_service.create_instance(
        workflow_id="manual_execution",
        user_id=user_id,
        initial_data=request.context
    )
    
    if not dag_instance:
        raise HTTPException(status_code=404, detail="Could not create execution context")
    
    # Find the task in the DAG
    dag = dag_instance.dag
    task = dag.get_task_by_id(request.step_id)
    
    if not task:
        # Create a temporary Python operator for manual execution
        from ...workflows.operators.python import PythonOperator
        
        def manual_execution_function(context):
            # This is a placeholder - in a real scenario, we'd need to know the actual function
            return {"manual_execution": True, "inputs": request.inputs, "timestamp": datetime.utcnow().isoformat()}
        
        task = PythonOperator(request.step_id, manual_execution_function)
    
    try:
        if request.dry_run:
            # Validation mode - check if task can run
            return {
                "step_id": request.step_id,
                "validation_result": {
                    "valid": True,
                    "message": f"Step {request.step_id} is ready for execution",
                    "operator_type": task.__class__.__name__
                },
                "dry_run": True
            }
        else:
            # Execute the task
            start_time = datetime.utcnow()
            
            # Set inputs for the task
            task.set_input(request.inputs)
            
            # Execute
            result_status = task.run(dag_instance.context)
            
            end_time = datetime.utcnow()
            duration = (end_time - start_time).total_seconds()
            
            # Create execution record
            execution = StepExecution(
                execution_id=str(uuid.uuid4()),
                instance_id=dag_instance.instance_id,
                step_id=request.step_id,
                workflow_id="manual_execution",
                status=result_status,
                inputs=request.inputs,
                outputs=task.get_output(),
                started_at=start_time,
                completed_at=end_time,
                duration_seconds=duration
            )
            
            await execution.insert()
            
            return {
                "step_id": request.step_id,
                "execution_result": {
                    "status": result_status,
                    "outputs": task.get_output(),
                    "duration_seconds": duration,
                    "execution_id": execution.execution_id
                },
                "dry_run": False
            }
            
    except Exception as e:
        # Record failed execution
        execution = StepExecution(
            execution_id=str(uuid.uuid4()),
            instance_id=dag_instance.instance_id,
            step_id=request.step_id,
            workflow_id="manual_execution",
            status="failed",
            inputs=request.inputs,
            outputs={},
            error_message=str(e),
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow()
        )
        
        await execution.insert()
        
        raise HTTPException(status_code=500, detail=f"Step execution failed: {str(e)}")


@router.post("/steps/validate")
async def validate_step(request: StepValidationRequest):
    """Validate a step without executing it (new DAG system)"""
    # Basic validation using manual execution in dry_run mode
    try:
        result = await execute_step_manually(
            StepExecutionRequest(
                step_id=request.step_id,
                inputs=request.inputs,
                context={},
                dry_run=True,
                environment="validation"
            ),
            user_id="system_validation"
        )
        
        return {
            "step_id": request.step_id,
            "validation_result": result["validation_result"],
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Step validation failed: {str(e)}")


@router.get("/workflows/{workflow_id}/performance", response_model=WorkflowPerformanceResponse)
async def get_workflow_performance(
    workflow_id: str,
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)")
):
    """Get comprehensive performance analysis for a workflow (new DAG system)"""
    from ...models.workflow import WorkflowInstance, StepExecution
    
    try:
        # Get DAG
        dag = await workflow_service.get_dag(workflow_id)
        if not dag:
            raise HTTPException(status_code=404, detail="Workflow not found")
        
        # Get all instances for this workflow
        instances = await WorkflowInstance.find(WorkflowInstance.workflow_id == workflow_id).to_list()
        
        # Collect metrics for all steps in the workflow
        step_metrics = []
        
        for task_id in dag.tasks.keys():
            try:
                metrics_response = await get_step_performance_metrics(task_id)
                step_metrics.append(metrics_response)
            except HTTPException:
                # Skip steps with no data
                continue
        
        # Calculate workflow-level metrics from instances
        total_instances = len(instances)
        completed_instances = len([i for i in instances if i.status == "completed"])
        completion_rate = (completed_instances / total_instances * 100) if total_instances > 0 else 0
        
        # Average completion time from completed instances
        completed_with_duration = [i for i in instances if i.duration_seconds is not None]
        average_completion_time = (
            sum(i.duration_seconds * 1000 for i in completed_with_duration) / len(completed_with_duration)
            if completed_with_duration else 0
        )
        
        # Workflow-level bottleneck analysis
        bottleneck_analysis = {
            "total_steps": len(dag.tasks),
            "steps_with_data": len(step_metrics),
            "total_instances": total_instances,
            "completed_instances": completed_instances,
            "average_step_success_rate": sum(m.success_rate for m in step_metrics) / len(step_metrics) if step_metrics else 0,
            "slowest_step": max(step_metrics, key=lambda x: x.average_duration_ms).step_id if step_metrics else None,
            "most_error_prone_step": min(step_metrics, key=lambda x: x.success_rate).step_id if step_metrics else None
        }
        
        # Generate optimization suggestions
        optimization_suggestions = []
        if step_metrics:
            slow_steps = [m for m in step_metrics if m.average_duration_ms > 5000]
            if slow_steps:
                optimization_suggestions.append(f"Consider optimizing slow steps: {[s.step_id for s in slow_steps]}")
            
            error_prone_steps = [m for m in step_metrics if m.success_rate < 90]
            if error_prone_steps:
                optimization_suggestions.append(f"Review error-prone steps: {[s.step_id for s in error_prone_steps]}")
        
        return WorkflowPerformanceResponse(
            workflow_id=workflow_id,
            total_instances=total_instances,
            completed_instances=completed_instances,
            completion_rate=round(completion_rate, 2),
            average_completion_time_ms=round(average_completion_time, 2),
            step_metrics=step_metrics,
            bottleneck_analysis=bottleneck_analysis,
            optimization_suggestions=optimization_suggestions
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get workflow performance: {str(e)}")


@router.get("/workflows/{workflow_id}/bottlenecks", response_model=BottleneckAnalysisResponse)
async def analyze_workflow_bottlenecks(
    workflow_id: str,
    threshold_ms: int = Query(5000, description="Threshold in milliseconds for slow steps"),
    min_executions: int = Query(10, description="Minimum executions required for analysis")
):
    """Analyze bottlenecks in a workflow (new DAG system)"""
    
    try:
        dag = await workflow_service.get_dag(workflow_id)
        if not dag:
            raise HTTPException(status_code=404, detail="Workflow not found")
        
        # Get performance data for analysis
        perf_data = await get_workflow_performance(workflow_id)
        
        # Analyze bottlenecks from step metrics
        slowest_steps = []
        approval_bottlenecks = []
        external_service_bottlenecks = []
        queue_bottlenecks = []
        recommendations = []
        
        for step_metric in perf_data.step_metrics:
            if step_metric.total_executions >= min_executions:
                # Slow steps
                if step_metric.average_duration_ms > threshold_ms:
                    slowest_steps.append({
                        "step_id": step_metric.step_id,
                        "step_name": step_metric.step_id.replace("_", " ").title(),
                        "average_duration_ms": step_metric.average_duration_ms,
                        "executions": step_metric.total_executions
                    })
                
                # Low success rate steps (potential approval bottlenecks)
                if step_metric.success_rate < 80:
                    approval_bottlenecks.append({
                        "step_id": step_metric.step_id,
                        "step_name": step_metric.step_id.replace("_", " ").title(),
                        "approval_bottleneck_percentage": 100 - step_metric.success_rate,
                        "executions": step_metric.total_executions
                    })
                
                # Add recommendations based on analysis
                if step_metric.average_duration_ms > threshold_ms:
                    recommendations.append(f"Optimize slow step: {step_metric.step_id}")
                if step_metric.success_rate < 90:
                    recommendations.append(f"Review error handling for: {step_metric.step_id}")
        
        # Sort by impact
        slowest_steps.sort(key=lambda x: x["average_duration_ms"] * x["executions"], reverse=True)
        
        # Critical path (top 3 slowest steps)
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
    """List all available workflows (new DAG system)"""
    # Get workflows from DAG bag
    workflows = []
    for dag_id, dag in workflow_service.dag_bag.dags.items():
        workflows.append({
            "workflow_id": dag_id,
            "name": dag.description or dag_id,
            "description": dag.description,
            "status": dag.status.value,
            "task_count": len(dag.tasks),
            "created_at": dag.created_at.isoformat(),
            "version": dag.version
        })
    
    return {"workflows": workflows}


@router.get("/steps")
async def list_steps(
    workflow_id: Optional[str] = Query(None, description="Filter steps by workflow")
):
    """List all available steps, optionally filtered by workflow"""
    try:
        if workflow_id:
            # Get steps for specific workflow using new DAG system
            dag = await workflow_service.get_dag(workflow_id)
            if not dag:
                raise HTTPException(status_code=404, detail="Workflow not found")
            
            steps = []
            for task_id, task in dag.tasks.items():
                steps.append({
                    "step_id": task_id,
                    "step_name": task_id.replace("_", " ").title(),
                    "step_type": task.__class__.__name__.replace("Operator", "").lower(),
                    "description": f"{task.__class__.__name__} operation",
                    "workflow_id": workflow_id,
                    "upstream_tasks": [t.task_id for t in task.upstream_tasks],
                    "downstream_tasks": [t.task_id for t in task.downstream_tasks]
                })
            
            return {"steps": steps}
        else:
            # Get all steps from all workflows
            all_steps = []
            for dag_id, dag in workflow_service.dag_bag.dags.items():
                for task_id, task in dag.tasks.items():
                    all_steps.append({
                        "step_id": task_id,
                        "step_name": task_id.replace("_", " ").title(),
                        "step_type": task.__class__.__name__.replace("Operator", "").lower(),
                        "description": f"{task.__class__.__name__} operation",
                        "workflow_id": dag_id,
                        "upstream_tasks": [t.task_id for t in task.upstream_tasks],
                        "downstream_tasks": [t.task_id for t in task.downstream_tasks]
                    })
            
            return {"steps": all_steps}
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list steps: {str(e)}")


@router.get("/stats")
async def get_workflow_stats():
    """Get overall workflow statistics"""
    try:
        from ...models.workflow import WorkflowInstance
        from datetime import datetime, timedelta
        
        # Get real statistics from database
        all_instances = await WorkflowInstance.find().to_list()
        
        # Calculate basic stats
        total_instances = len(all_instances)
        active_instances = len([i for i in all_instances if i.status in ["running", "awaiting_input"]])
        
        # Get completed today
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        completed_today = len([i for i in all_instances if i.completed_at and i.completed_at >= today])
        
        # Calculate average completion time from completed instances
        completed_instances = [i for i in all_instances if i.duration_seconds is not None]
        avg_completion_time_hours = (
            sum(i.duration_seconds for i in completed_instances) / 3600 / len(completed_instances)
            if completed_instances else 0
        )
        
        # Success rate
        finished_instances = [i for i in all_instances if i.status in ["completed", "failed"]]
        success_rate = (
            len([i for i in finished_instances if i.status == "completed"]) / len(finished_instances)
            if finished_instances else 0
        )
        
        # Group by workflow
        by_workflow = {}
        for instance in all_instances:
            workflow_id = instance.workflow_id
            by_workflow[workflow_id] = by_workflow.get(workflow_id, 0) + 1
        
        # Group by status
        by_status = {}
        for instance in all_instances:
            status = instance.status
            by_status[status] = by_status.get(status, 0) + 1
        
        stats = {
            "total_instances": total_instances,
            "active_instances": active_instances,
            "completed_today": completed_today,
            "avg_completion_time_hours": round(avg_completion_time_hours, 2),
            "success_rate": round(success_rate, 2),
            "by_workflow": by_workflow,
            "by_status": by_status
        }
        
        return stats
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")


@router.get("/health")
async def performance_service_health():
    """Health check for performance monitoring service"""
    try:
        from ...models.workflow import StepExecution
        
        # Get real health data from new DAG system
        total_executions = await StepExecution.find().count()
        active_dag_instances = len(workflow_service.dag_bag.instances)
        registered_dags = len(workflow_service.dag_bag.dags)
        
        return {
            "status": "healthy",
            "service": "performance_monitoring",
            "registered_dags": registered_dags,
            "active_instances": active_dag_instances,
            "total_tracked_executions": total_executions,
            "executor_running": workflow_service.executor.is_running if hasattr(workflow_service.executor, 'is_running') else True
        }
        
    except Exception as e:
        return {
            "status": "unhealthy", 
            "service": "performance_monitoring",
            "error": str(e)
        }