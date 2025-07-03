"""
Step execution engine with performance monitoring and validation.
"""

import time
import psutil
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime
from uuid import uuid4

from .base import BaseStep, StepResult, StepStatus, ValidationResult
from .workflow import WorkflowInstance


class StepExecutionContext:
    """Context for step execution with timing and monitoring"""
    
    def __init__(self, 
                 instance_id: str,
                 user_id: str,
                 execution_environment: str = "production"):
        self.instance_id = instance_id
        self.user_id = user_id
        self.execution_environment = execution_environment
        self.queued_at: Optional[datetime] = None
        self.execution_id = str(uuid4())
        
    def mark_queued(self):
        """Mark when the step was queued for execution"""
        self.queued_at = datetime.utcnow()


class StepExecutor:
    """Advanced step executor with performance monitoring"""
    
    def __init__(self):
        self.execution_metrics: Dict[str, List[StepResult]] = {}
        
    async def execute_step(self, 
                          step: BaseStep,
                          inputs: Dict[str, Any],
                          context: Dict[str, Any],
                          execution_context: StepExecutionContext) -> StepResult:
        """Execute a single step with comprehensive monitoring"""
        
        result = StepResult(
            step_id=step.step_id,
            status=StepStatus.PENDING,
            executed_by=execution_context.user_id,
            execution_environment=execution_context.execution_environment,
            step_version="1.0.0"
        )
        
        # Calculate queue time
        if execution_context.queued_at:
            queue_duration = (datetime.utcnow() - execution_context.queued_at).total_seconds() * 1000
            result.queue_time_ms = int(queue_duration)
        
        # Start execution timing
        start_time = time.time()
        memory_before = self._get_memory_usage()
        
        result.started_at = datetime.utcnow()
        result.status = StepStatus.RUNNING
        
        try:
            # Validation phase
            validation_start = time.time()
            validation_result = await self._validate_step_inputs(step, inputs)
            validation_duration = (time.time() - validation_start) * 1000
            result.validation_duration_ms = int(validation_duration)
            
            if not validation_result.is_valid:
                result.status = StepStatus.FAILED
                result.error = f"Validation failed: {', '.join(validation_result.errors)}"
                result.completed_at = datetime.utcnow()
                result.calculate_duration()
                return result
            
            # Check for blocking dependencies
            blocking_deps = await self._check_blocking_dependencies(step, context)
            if blocking_deps:
                result.blocking_dependencies = blocking_deps
                result.status = StepStatus.PENDING
                return result
            
            # Execute the step
            step_outputs = await self._execute_step_logic(step, inputs, context)
            
            # Check if step is waiting for external dependencies
            if hasattr(step, 'approvers'):
                result.waiting_for_approval = True
            elif hasattr(step, 'service_name'):
                result.waiting_for_external_service = True
            
            result.outputs = step_outputs
            result.status = StepStatus.COMPLETED
            
        except Exception as e:
            result.status = StepStatus.FAILED
            result.error = str(e)
            
        finally:
            # Calculate performance metrics
            result.completed_at = datetime.utcnow()
            result.calculate_duration()
            
            memory_after = self._get_memory_usage()
            if memory_before and memory_after:
                result.memory_usage_mb = memory_after - memory_before
            
            # Store metrics for analysis
            self._store_execution_metrics(result)
        
        return result
    
    async def validate_step_execution(self, 
                                    step: BaseStep,
                                    inputs: Dict[str, Any],
                                    dry_run: bool = True) -> ValidationResult:
        """Validate step execution without actually running it"""
        errors = []
        
        # Check required inputs
        for required_input in step.required_inputs:
            if required_input not in inputs:
                errors.append(f"Missing required input: {required_input}")
        
        # Run validation functions
        for validation_func in step.validations:
            try:
                validation_result = validation_func(inputs)
                if isinstance(validation_result, ValidationResult):
                    if not validation_result.is_valid:
                        errors.extend(validation_result.errors)
                elif not validation_result:
                    errors.append(f"Validation failed: {validation_func.__name__}")
            except Exception as e:
                errors.append(f"Validation error in {validation_func.__name__}: {str(e)}")
        
        # Check step-specific validation
        if hasattr(step, 'validate_execution'):
            try:
                step_validation = await step.validate_execution(inputs, dry_run)
                if isinstance(step_validation, ValidationResult):
                    if not step_validation.is_valid:
                        errors.extend(step_validation.errors)
            except Exception as e:
                errors.append(f"Step validation error: {str(e)}")
        
        return ValidationResult(is_valid=len(errors) == 0, errors=errors)
    
    async def _validate_step_inputs(self, step: BaseStep, inputs: Dict[str, Any]) -> ValidationResult:
        """Internal validation of step inputs"""
        return step.validate_inputs(inputs)
    
    async def _check_blocking_dependencies(self, step: BaseStep, context: Dict[str, Any]) -> List[str]:
        """Check for blocking dependencies that prevent execution"""
        blocking = []
        
        # Check if previous steps are completed
        if hasattr(step, 'depends_on'):
            for dependency in step.depends_on:
                if context.get(f"{dependency}_completed") != True:
                    blocking.append(dependency)
        
        # Check external service availability
        if hasattr(step, 'service_name'):
            # In a real implementation, check service health
            pass
        
        return blocking
    
    async def _execute_step_logic(self, step: BaseStep, inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the actual step logic"""
        if hasattr(step, 'execute'):
            return await step.execute(inputs, context)
        else:
            # Default execution for basic steps
            return {"executed": True, "timestamp": datetime.utcnow().isoformat()}
    
    def _get_memory_usage(self) -> Optional[float]:
        """Get current memory usage in MB"""
        try:
            process = psutil.Process()
            return process.memory_info().rss / 1024 / 1024  # Convert to MB
        except:
            return None
    
    def _store_execution_metrics(self, result: StepResult):
        """Store execution metrics for analysis"""
        if result.step_id not in self.execution_metrics:
            self.execution_metrics[result.step_id] = []
        self.execution_metrics[result.step_id].append(result)
        
        # Keep only last 100 executions per step
        if len(self.execution_metrics[result.step_id]) > 100:
            self.execution_metrics[result.step_id] = self.execution_metrics[result.step_id][-100:]
    
    def get_step_performance_metrics(self, step_id: str) -> Dict[str, Any]:
        """Get performance metrics for a specific step"""
        if step_id not in self.execution_metrics:
            return {"error": "No execution data found for step"}
        
        executions = self.execution_metrics[step_id]
        successful_executions = [e for e in executions if e.status == StepStatus.COMPLETED]
        
        if not successful_executions:
            return {"error": "No successful executions found"}
        
        durations = [e.execution_duration_ms for e in successful_executions if e.execution_duration_ms]
        queue_times = [e.queue_time_ms for e in successful_executions if e.queue_time_ms]
        validation_times = [e.validation_duration_ms for e in successful_executions if e.validation_duration_ms]
        
        return {
            "step_id": step_id,
            "total_executions": len(executions),
            "successful_executions": len(successful_executions),
            "success_rate": len(successful_executions) / len(executions) * 100,
            "average_duration_ms": sum(durations) / len(durations) if durations else 0,
            "min_duration_ms": min(durations) if durations else 0,
            "max_duration_ms": max(durations) if durations else 0,
            "average_queue_time_ms": sum(queue_times) / len(queue_times) if queue_times else 0,
            "average_validation_time_ms": sum(validation_times) / len(validation_times) if validation_times else 0,
            "common_errors": self._get_common_errors(executions),
            "bottleneck_indicators": self._analyze_bottlenecks(successful_executions)
        }
    
    def _get_common_errors(self, executions: List[StepResult]) -> List[Dict[str, Any]]:
        """Analyze common error patterns"""
        error_counts = {}
        for execution in executions:
            if execution.error:
                error_counts[execution.error] = error_counts.get(execution.error, 0) + 1
        
        return [{"error": error, "count": count} for error, count in 
                sorted(error_counts.items(), key=lambda x: x[1], reverse=True)[:5]]
    
    def _analyze_bottlenecks(self, executions: List[StepResult]) -> Dict[str, Any]:
        """Analyze potential bottlenecks"""
        approval_waiting = sum(1 for e in executions if e.waiting_for_approval)
        external_waiting = sum(1 for e in executions if e.waiting_for_external_service)
        
        long_queue_times = sum(1 for e in executions if e.queue_time_ms and e.queue_time_ms > 5000)
        
        return {
            "approval_bottleneck_percentage": approval_waiting / len(executions) * 100 if executions else 0,
            "external_service_bottleneck_percentage": external_waiting / len(executions) * 100 if executions else 0,
            "long_queue_time_percentage": long_queue_times / len(executions) * 100 if executions else 0,
            "suggested_optimizations": self._suggest_optimizations(executions)
        }
    
    def _suggest_optimizations(self, executions: List[StepResult]) -> List[str]:
        """Suggest optimizations based on execution patterns"""
        suggestions = []
        
        if not executions:
            return suggestions
        
        avg_duration = sum(e.execution_duration_ms for e in executions if e.execution_duration_ms) / len(executions)
        avg_queue_time = sum(e.queue_time_ms for e in executions if e.queue_time_ms) / len(executions)
        
        if avg_duration > 10000:  # 10 seconds
            suggestions.append("Consider optimizing step logic - execution time is high")
        
        if avg_queue_time > 5000:  # 5 seconds
            suggestions.append("Consider adding more execution workers - queue time is high")
        
        approval_rate = sum(1 for e in executions if e.waiting_for_approval) / len(executions)
        if approval_rate > 0.5:
            suggestions.append("Consider streamlining approval process - many steps waiting for approval")
        
        return suggestions
    
    def _get_current_timestamp(self) -> str:
        """Get current timestamp in ISO format"""
        return datetime.utcnow().isoformat()


# Global executor instance
step_executor = StepExecutor()