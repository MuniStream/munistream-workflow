# Enhanced WorkflowStartOperator - Keycloak Integration

The `WorkflowStartOperator` has been enhanced to support automatic assignment of workflows to specific users within Keycloak groups, with round-robin assignment and auto-start capabilities.

## New Features

### 1. Auto-Assignment (`auto_assign`)
When enabled, the operator will automatically select a specific user from the Keycloak group instead of just assigning to the team.

### 2. Role-Based Assignment (`assignee_role`)
Filter group members by their Keycloak realm role (e.g., "reviewer", "approver", "manager").

### 3. Auto-Start (`auto_start`)
Automatically start workflow execution after assignment, eliminating the need for manual start.

### 4. Assignment Strategies (`assignment_strategy`)
Currently supports:
- `round_robin`: Evenly distribute assignments among eligible users

## Usage Examples

### Basic Enhanced Usage
```python
from app.workflows.operators.workflow_start_operator import WorkflowStartOperator
from app.models.workflow import WorkflowType

# Auto-assign to reviewer with auto-start
validate_documents = WorkflowStartOperator(
    task_id="validate_documents",
    workflow_id="admin_document_validation",
    workflow_type=WorkflowType.ADMIN,
    assign_to={"team": "document_reviewers"},  # Keycloak group name
    auto_assign=True,                          # Enable specific user assignment
    assignee_role="reviewer",                  # Only assign to users with 'reviewer' role
    auto_start=True,                          # Start automatically after assignment
    wait_for_completion=True
)
```

### Advanced Usage with Context Mapping
```python
# Property validation with complex assignment
validate_property = WorkflowStartOperator(
    task_id="validate_property_catastro",
    workflow_id="catastro_property_validation",
    workflow_type=WorkflowType.ADMIN,
    assign_to={"team": "catastro_validators"},
    auto_assign=True,
    assignee_role="validator",
    assignment_strategy="round_robin",
    auto_start=True,
    timeout_minutes=2880,  # 48 hours
    context_mapping={
        "property_id": "property_id",
        "documents": "uploaded_files",
        "citizen_data": "applicant_info"
    },
    wait_for_completion=True,
    required_status="approved"
)
```

### Workflow Without Auto-Assignment (Original Behavior)
```python
# Traditional team assignment - no changes needed
approve_permit = WorkflowStartOperator(
    task_id="approve_permit",
    workflow_id="permit_approval",
    workflow_type=WorkflowType.ADMIN,
    assign_to={"team": "permit_approvers"},  # Assigns to team only
    wait_for_completion=True
)
```

## Parameter Reference

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `auto_assign` | bool | False | Enable automatic user selection from group |
| `assignee_role` | str | None | Required Keycloak realm role for assignee |
| `auto_start` | bool | False | Automatically start workflow after assignment |
| `assignment_strategy` | str | "round_robin" | Strategy for user selection |

## How It Works

### Assignment Flow
1. **Group Assignment**: Workflow is always assigned to the specified team/group
2. **User Selection** (if `auto_assign=True`):
   - Retrieve all members of the Keycloak group
   - Filter members by `assignee_role` (if specified)
   - Use `assignment_strategy` to select specific user
   - Assign workflow to both team AND selected user
3. **Auto-Start** (if `auto_start=True` and user assigned):
   - Set status to "running" and assignment_status to "under_review"
   - Trigger workflow execution automatically

### Round-Robin Implementation
- Maintains state per group/role/workflow combination
- Cycles through eligible users in order
- Handles group membership changes gracefully
- Thread-safe for concurrent assignments

### Error Handling
- Auto-assignment failures fall back to team-only assignment
- Auto-start failures don't affect assignment success
- Comprehensive logging for debugging

## Keycloak Requirements

### Group Setup
- Groups must exist in Keycloak realm
- Group names should match the `team` parameter value

### Role Configuration
- Users must have appropriate realm roles assigned
- Role names must match the `assignee_role` parameter

### Permissions
- Service account needs admin permissions to:
  - Read groups and group membership
  - Read user details and roles
  - Query realm roles

## Monitoring and Debugging

### Logging
The operator provides detailed logging at multiple levels:
- INFO: Assignment decisions and outcomes
- DEBUG: User selection process
- ERROR: Assignment and auto-start failures

### Round-Robin State
Access assignment statistics:
```python
from app.services.keycloak_group_assignment import keycloak_group_assignment_service

# Get current round-robin state
stats = await keycloak_group_assignment_service.get_assignment_stats()
print(stats)

# Reset round-robin state (for debugging)
keycloak_group_assignment_service.reset_round_robin_state(group_id="validators")
```

## Best Practices

1. **Group Management**: Keep Keycloak groups synchronized with team structures
2. **Role Consistency**: Use standardized role names across tenants
3. **Monitoring**: Monitor round-robin distribution to ensure fairness
4. **Fallback**: Always test with `auto_assign=False` as backup
5. **Performance**: Consider caching for large groups with many members

## Migration Guide

### From Original WorkflowStartOperator
No changes needed! All existing code continues to work unchanged. New features are opt-in.

### Adding Auto-Assignment
1. Set `auto_assign=True`
2. Optionally specify `assignee_role`
3. Test with existing Keycloak groups
4. Enable `auto_start` when ready

### Troubleshooting
- Check Keycloak group membership and roles
- Verify service account permissions
- Review logs for assignment decisions
- Test with simple group/role combinations first