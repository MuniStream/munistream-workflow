# CivicStream Performance Monitoring System

## Overview

The CivicStream Performance Monitoring System allows administrators to measure step-to-step execution times, identify bottlenecks, and optimize workflow performance. This feature addresses the requirement to **"measure how long it takes citizens to get from a node to another node so administrators can see where are the bottlenecks"**.

## Features

### 🎯 Core Capabilities

1. **Step-by-Step Timing Analysis**
   - Measure execution duration for each workflow step
   - Track queue times (time waiting before execution)
   - Monitor validation overhead
   - Record memory usage during execution

2. **Bottleneck Detection**
   - Identify slow-executing steps
   - Detect approval process delays
   - Monitor external service dependencies
   - Analyze queue backlogs

3. **Manual Step Execution**
   - Run individual steps in isolation
   - Validate step inputs without execution (dry-run mode)
   - Test step logic with custom inputs
   - Debug workflow issues

4. **Performance Analytics**
   - Success/failure rates per step
   - Average, min, max execution times
   - Common error patterns
   - Optimization recommendations

## API Endpoints

### Base URL: `/api/v1/performance`

### 📊 Analytics Endpoints

#### `GET /workflows`
List all available workflows with basic information.

**Response:**
```json
{
  "workflows": [
    {
      "workflow_id": "citizen_registration_v1",
      "name": "Citizen Registration",
      "description": "Complete workflow for registering new citizens",
      "step_count": 17
    }
  ]
}
```

#### `GET /steps?workflow_id={id}`
List all steps, optionally filtered by workflow.

**Response:**
```json
{
  "steps": [
    {
      "step_id": "validate_identity",
      "name": "Validate Identity",
      "type": "ActionStep",
      "workflow_id": "citizen_registration_v1"
    }
  ]
}
```

#### `GET /steps/{step_id}/metrics`
Get comprehensive performance metrics for a specific step.

**Response:**
```json
{
  "step_id": "validate_identity",
  "total_executions": 1000,
  "successful_executions": 950,
  "success_rate": 95.0,
  "average_duration_ms": 150.5,
  "min_duration_ms": 50,
  "max_duration_ms": 2000,
  "average_queue_time_ms": 25.0,
  "average_validation_time_ms": 10.2,
  "common_errors": [
    {
      "error": "Missing required input: id_number",
      "count": 30
    }
  ],
  "bottleneck_indicators": {
    "approval_bottleneck_percentage": 15.0,
    "external_service_bottleneck_percentage": 5.0,
    "long_queue_time_percentage": 2.0,
    "suggested_optimizations": [
      "Consider optimizing step logic - execution time is high"
    ]
  }
}
```

### 🔧 Execution Endpoints

#### `POST /steps/execute`
Manually execute a specific step with performance monitoring.

**Request:**
```json
{
  "step_id": "validate_identity",
  "inputs": {
    "first_name": "John",
    "last_name": "Doe",
    "id_number": "123456789",
    "id_document": "passport"
  },
  "context": {},
  "dry_run": false,
  "environment": "manual"
}
```

**Response:**
```json
{
  "step_id": "validate_identity",
  "execution_result": {
    "step_id": "validate_identity",
    "status": "completed",
    "outputs": {
      "identity_verified": true,
      "verification_method": "document_scan",
      "confidence_score": 0.95
    },
    "execution_duration_ms": 145,
    "queue_time_ms": 12,
    "validation_duration_ms": 8,
    "memory_usage_mb": 2.5
  },
  "dry_run": false
}
```

#### `POST /steps/validate`
Validate step inputs without executing the step logic.

**Request:**
```json
{
  "step_id": "validate_identity",
  "inputs": {
    "first_name": "John",
    "last_name": "Doe"
  },
  "dry_run": true
}
```

**Response:**
```json
{
  "step_id": "validate_identity",
  "validation_result": {
    "is_valid": false,
    "errors": [
      "Missing required input: id_number",
      "Missing required input: id_document"
    ]
  },
  "timestamp": "2025-07-03T10:30:00Z"
}
```

### 📈 Analysis Endpoints

#### `GET /workflows/{workflow_id}/performance`
Get comprehensive performance analysis for an entire workflow.

**Response:**
```json
{
  "workflow_id": "citizen_registration_v1",
  "total_instances": 10000,
  "completed_instances": 8500,
  "completion_rate": 85.0,
  "average_completion_time_ms": 300000,
  "step_metrics": [...],
  "bottleneck_analysis": {
    "total_steps": 17,
    "steps_with_data": 15,
    "average_step_success_rate": 92.5,
    "slowest_step": "adult_approval",
    "most_error_prone_step": "blockchain_record"
  },
  "optimization_suggestions": [
    "Consider streamlining approval process - many steps waiting for approval",
    "Consider adding more execution workers - queue time is high"
  ]
}
```

#### `GET /workflows/{workflow_id}/bottlenecks`
Analyze bottlenecks in a workflow with detailed recommendations.

**Query Parameters:**
- `threshold_ms` (default: 5000): Threshold for slow steps
- `min_executions` (default: 10): Minimum executions for analysis

**Response:**
```json
{
  "workflow_id": "citizen_registration_v1",
  "critical_path_steps": ["adult_approval", "blockchain_record", "send_welcome"],
  "slowest_steps": [
    {
      "step_id": "adult_approval",
      "step_name": "Adult Registration Approval",
      "average_duration_ms": 86400000,
      "executions": 5000
    }
  ],
  "approval_bottlenecks": [
    {
      "step_id": "adult_approval",
      "step_name": "Adult Registration Approval",
      "approval_bottleneck_percentage": 85.0,
      "executions": 5000
    }
  ],
  "external_service_bottlenecks": [
    {
      "step_id": "blockchain_record",
      "step_name": "Record on Blockchain",
      "external_service_bottleneck_percentage": 25.0,
      "executions": 3000
    }
  ],
  "queue_bottlenecks": [],
  "recommendations": [
    "Adult Registration Approval: Consider streamlining approval process - many steps waiting for approval",
    "Record on Blockchain: Consider optimizing step logic - execution time is high"
  ]
}
```

#### `GET /workflows/{workflow_id}/timing-analysis`
Get detailed timing analysis showing citizen wait times between steps.

**Response:**
```json
{
  "workflow_id": "citizen_registration_v1",
  "instance_id": null,
  "step_transitions": [
    {
      "from_step": "validate_identity",
      "to_step": "identity_check",
      "average_transition_time_ms": 150,
      "citizen_wait_time_ms": 0
    },
    {
      "from_step": "adult_approval",
      "to_step": "process_approval",
      "average_transition_time_ms": 86400000,
      "citizen_wait_time_ms": 86400000
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
  "citizen_active_time_ms": 5000,
  "citizen_waiting_time_ms": 86400000
}
```

### 🔍 Health Check

#### `GET /health`
Check the health of the performance monitoring service.

**Response:**
```json
{
  "status": "healthy",
  "service": "performance_monitoring",
  "executor_metrics_count": 15,
  "total_tracked_executions": 10000
}
```

## Implementation Details

### Core Components

1. **StepExecutor** (`app/workflows/executor.py`)
   - Handles step execution with comprehensive monitoring
   - Tracks timing, memory usage, and bottleneck indicators
   - Provides validation without execution

2. **StepRegistry** (`app/workflows/registry.py`)
   - Manages workflow and step lookup
   - Auto-registers example workflows
   - Provides listing and search capabilities

3. **Performance API** (`app/api/v1/performance.py`)
   - RESTful endpoints for performance monitoring
   - Pydantic models for request/response validation
   - Integration with StepExecutor and StepRegistry

### Enhanced StepResult Model

The `StepResult` model includes comprehensive performance metrics:

```python
class StepResult(BaseModel):
    # Basic execution info
    step_id: str
    status: StepStatus
    outputs: Dict[str, Any]
    error: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    
    # Performance metrics
    execution_duration_ms: Optional[int]
    queue_time_ms: Optional[int]
    validation_duration_ms: Optional[int]
    retry_count: int
    memory_usage_mb: Optional[float]
    
    # Execution context
    executed_by: Optional[str]
    execution_environment: Optional[str]
    step_version: Optional[str]
    
    # Bottleneck analysis
    waiting_for_approval: bool
    waiting_for_external_service: bool
    blocking_dependencies: List[str]
```

## Usage Examples

### 1. Monitor Step Performance

```bash
# Get metrics for a specific step
curl "http://localhost:8000/api/v1/performance/steps/validate_identity/metrics"
```

### 2. Execute Step Manually

```bash
# Execute a step with custom inputs
curl -X POST "http://localhost:8000/api/v1/performance/steps/execute?user_id=admin" \
     -H "Content-Type: application/json" \
     -d '{
       "step_id": "validate_identity",
       "inputs": {
         "first_name": "John",
         "last_name": "Doe",
         "id_number": "123456789",
         "id_document": "passport"
       }
     }'
```

### 3. Analyze Workflow Bottlenecks

```bash
# Get bottleneck analysis for a workflow
curl "http://localhost:8000/api/v1/performance/workflows/citizen_registration_v1/bottlenecks?threshold_ms=1000"
```

### 4. Validate Step Inputs

```bash
# Validate step inputs without execution
curl -X POST "http://localhost:8000/api/v1/performance/steps/validate" \
     -H "Content-Type: application/json" \
     -d '{
       "step_id": "check_duplicates",
       "inputs": {
         "email": "invalid-email"
       }
     }'
```

## Testing

Run the demonstration script to see the performance monitoring in action:

```bash
python test_performance_monitoring.py
```

This will:
- Load available workflows
- Simulate step executions
- Display performance metrics
- Show bottleneck analysis

## Integration with Docker

The performance monitoring system is automatically included when running the CivicStream backend:

```bash
# Start the full stack
docker-compose up -d

# Access performance API
curl "http://localhost:8000/api/v1/performance/health"
```

## Future Enhancements

1. **Real-time Monitoring Dashboard**
   - WebSocket-based live performance updates
   - Visual charts and graphs
   - Alerting for performance degradation

2. **Advanced Analytics**
   - Machine learning-based bottleneck prediction
   - Seasonal performance pattern analysis
   - Comparative performance across environments

3. **Integration Features**
   - Export performance data to external monitoring systems
   - Integration with Prometheus/Grafana
   - Custom performance alerts and notifications

## Benefits

- **Improved Citizen Experience**: Identify and eliminate delays in government processes
- **Resource Optimization**: Understand where to allocate computing resources
- **Process Improvement**: Data-driven insights for workflow optimization
- **Troubleshooting**: Quickly identify and resolve performance issues
- **Capacity Planning**: Understand system limits and scaling requirements

The performance monitoring system provides administrators with the tools they need to ensure CivicStream workflows run efficiently and citizens experience minimal delays in their interactions with government services.