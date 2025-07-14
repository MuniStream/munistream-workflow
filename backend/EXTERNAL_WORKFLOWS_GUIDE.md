# External Workflow Development Guide

This guide explains how to create custom workflows for CivicStream in your own repository.

## Overview

CivicStream supports loading workflows from external Git repositories, allowing organizations to:
- Maintain proprietary workflows separately from the core platform
- Version control their business processes independently
- Share workflows between multiple CivicStream instances
- Keep sensitive business logic private

## Quick Start

### 1. Create Your Workflow Repository

Create a new Git repository with the following structure:

```
your-workflows/
├── civicstream.yaml          # Plugin configuration (required)
├── requirements.txt          # Python dependencies (optional)
├── your_org/
│   ├── __init__.py
│   └── workflows.py          # Your workflow definitions
└── tests/
    └── test_workflows.py     # Workflow tests (recommended)
```

### 2. Configure Your Plugin

Create `civicstream.yaml` in your repository root:

```yaml
name: your-org-workflows
version: 1.0.0
description: Custom workflows for Your Organization

workflows:
  - module: your_org.workflows
    function: create_procurement_workflow
  - module: your_org.workflows
    function: create_vendor_approval_workflow
```

### 3. Create Your Workflows

In `your_org/workflows.py`:

```python
from civicstream.workflows.base import (
    ActionStep, ConditionalStep, ApprovalStep, 
    IntegrationStep, TerminalStep
)
from civicstream.workflows.workflow import Workflow

def create_procurement_workflow() -> Workflow:
    workflow = Workflow(
        workflow_id="procurement_v1",
        name="Procurement Request",
        description="Handle procurement requests with approval chain"
    )
    
    # Define your steps...
    step_submit = ActionStep(
        step_id="submit_request",
        name="Submit Procurement Request",
        action=validate_procurement_request,
        required_inputs=["item_description", "quantity", "estimated_cost"]
    )
    
    # Add more steps and logic...
    
    return workflow
```

### 4. Register Your Plugin with CivicStream

#### Option A: Via API (Recommended)
```bash
curl -X POST http://localhost:8000/api/v1/plugins/add \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "your-org-workflows",
    "repo_url": "https://github.com/your-org/your-workflows"
  }'
```

#### Option B: Via Configuration File
Add to `plugins.yaml` in CivicStream:
```yaml
plugins:
  - name: your-org-workflows
    repo_url: https://github.com/your-org/your-workflows
    enabled: true
```

## Workflow Development

### Step Types

CivicStream provides several built-in step types:

#### ActionStep
Executes custom Python code:
```python
def validate_data(instance, context):
    # Your validation logic
    return {"status": "valid", "data": processed_data}

step = ActionStep(
    step_id="validate",
    name="Validate Data",
    action=validate_data,
    required_inputs=["field1", "field2"]
)
```

#### ConditionalStep
Routes workflow based on conditions:
```python
step = ConditionalStep(
    step_id="check_amount",
    name="Check Amount Threshold"
)

# Define routing
step.when(lambda i, c: i.data["amount"] > 1000) >> approval_step
step.when(lambda i, c: i.data["amount"] <= 1000) >> auto_approve_step
```

#### ApprovalStep
Requires human approval:
```python
step = ApprovalStep(
    step_id="manager_approval",
    name="Manager Approval Required",
    approvers=["manager_role", "specific_user@example.com"]
)
```

#### IntegrationStep
Calls external services:
```python
step = IntegrationStep(
    step_id="payment_process",
    name="Process Payment",
    service="payment_gateway",
    endpoint="/process",
    method="POST"
)
```

#### TerminalStep
Ends the workflow:
```python
step = TerminalStep(
    step_id="completed",
    name="Process Completed",
    description="Workflow completed successfully"
)
```

### Best Practices

1. **Unique IDs**: Ensure all workflow and step IDs are unique
2. **Error Handling**: Always include error terminal steps
3. **Documentation**: Document required inputs and outputs
4. **Testing**: Include unit tests for your workflows
5. **Versioning**: Version your workflows (e.g., `procurement_v1`, `procurement_v2`)

### Example: Complete Fishing Permit Workflow

See `/app/workflows/examples/fishing_permit.py` for a complete example including:
- Identity verification
- Document validation
- Fee calculation
- Payment processing
- Approval chain
- Permit generation
- Blockchain recording

## Testing Your Workflows

Create tests in `tests/test_workflows.py`:

```python
import pytest
from your_org.workflows import create_procurement_workflow

def test_procurement_workflow_creation():
    workflow = create_procurement_workflow()
    assert workflow.workflow_id == "procurement_v1"
    assert len(workflow.steps) > 0
    
def test_procurement_workflow_validation():
    workflow = create_procurement_workflow()
    workflow.build_graph()
    workflow.validate()  # Should not raise
```

## Deployment

### Private Repositories

For private repositories, configure authentication:

1. **SSH Key**: Add deploy key to your repository
2. **Personal Access Token**: Use HTTPS with token
3. **GitHub App**: Configure CivicStream as GitHub App

### Environment Variables

```bash
# Plugin storage location
CIVICSTREAM_PLUGINS_PATH=/var/civicstream/plugins

# Plugin configuration file
CIVICSTREAM_PLUGINS_CONFIG=/etc/civicstream/plugins.yaml

# Git credentials (for private repos)
GIT_USERNAME=your-username
GIT_TOKEN=your-personal-access-token
```

## Advanced Features

### Dynamic Step Creation

Create steps dynamically based on data:

```python
def create_multi_approval_workflow(approval_levels: List[str]) -> Workflow:
    workflow = Workflow(...)
    
    previous_step = initial_step
    for level in approval_levels:
        approval = ApprovalStep(
            step_id=f"approval_{level}",
            name=f"{level.title()} Approval",
            approvers=[f"{level}_role"]
        )
        previous_step >> approval
        previous_step = approval
    
    return workflow
```

### Custom Step Types

Extend base step types for specialized behavior:

```python
from civicstream.workflows.base import ActionStep

class EmailNotificationStep(ActionStep):
    def __init__(self, step_id, name, recipients, template, **kwargs):
        super().__init__(
            step_id=step_id,
            name=name,
            action=self._send_email,
            **kwargs
        )
        self.recipients = recipients
        self.template = template
        
    def _send_email(self, instance, context):
        # Email sending logic
        return {"sent": True, "recipients": self.recipients}
```

### Workflow Composition

Reuse common patterns:

```python
def add_payment_flow(workflow, after_step, amount_field="total_amount"):
    calculate = ActionStep(...)
    payment = IntegrationStep(...)
    verify = ConditionalStep(...)
    
    after_step >> calculate >> payment >> verify
    return verify  # Return last step for chaining
```

## Troubleshooting

### Common Issues

1. **Import Errors**: Ensure your module structure is correct
2. **Missing Dependencies**: Add all requirements to `requirements.txt`
3. **ID Conflicts**: Use unique prefixes for your organization
4. **Validation Errors**: Check all steps are connected properly

### Debugging

Enable debug logging:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Support

- Documentation: [CivicStream Docs](https://docs.civicstream.io)
- Examples: See `/app/workflows/examples/`
- Issues: Open issue in your repository or CivicStream core