# CivicStream Project Context

## Current Status (Last Updated: 2025-07-10)

### Project Overview
CivicStream is a DAG-based workflow automation platform for government services, starting with building permit workflows. The system has:
- **Backend**: FastAPI with MongoDB, complete authentication system with JWT tokens
- **Frontend**: React 18 with TypeScript, Material-UI v7, fully containerized
- **Repositories**: 
  - Backend: https://github.com/paw-ml/civicstream-workflow
  - Frontend: https://github.com/paw-ml/civicstream-admin-frontend

### Completed Features
1. ✅ Complete authentication system with 5 roles and 12 permissions
2. ✅ Document verification system with AI analysis
3. ✅ Workflow visualization with Mermaid.js
4. ✅ Admin inbox for approvals and reviews
5. ✅ Real MongoDB persistence (no mock data)
6. ✅ Frontend fully containerized and pushed to GitHub

### GitHub Projects (Kanban Boards)
- **Project #1**: CivicStream Frontend Development (6 issues in Todo)
- **Project #2**: CivicStream Backend Development (5 issues in Todo)

### Current Task
Was creating GitHub issues for the kanban boards. Encountered shell environment error. Still need to create:
- Frontend Issue #9: 🟢 LOW: Create Mobile-Responsive Views
- Frontend Issue #10: 🟢 LOW: Add Bulk Operations

### Tomorrow's Priorities
1. 🔴 HIGH: Create Citizen Instance Tracking System (Frontend Issue #1, Backend Issue #2)
2. 🔴 HIGH: Add Real-time Notifications & WebSocket Integration (Frontend Issue #2, Backend Issue #1)
3. 🟡 MEDIUM: Create User Management Interface (Frontend Issue #3)

### Development Commands
```bash
# Backend
cd /Users/paw/Projects/CivicStream/backend
docker-compose up

# Frontend
cd /Users/paw/Projects/CivicStream/civicstream-admin-frontend
docker-compose up

# Access
Frontend: http://localhost:3000
Backend API: http://localhost:8000
API Docs: http://localhost:8000/docs
```

### Test Users
- admin / admin123 (Admin role - all permissions)
- manager / manager123 (Manager role)
- reviewer / reviewer123 (Reviewer role)
- approver / approver123 (Approver role)
- viewer / viewer123 (Viewer role)

### Key Technical Decisions
- Separate repositories for frontend and backend
- Docker containers for both services
- JWT authentication with refresh tokens
- MongoDB with Beanie ODM
- React Query for data fetching
- Material-UI v7 for components
- Mermaid.js for workflow visualization

### Workflow Creation Pattern
**IMPORTANT**: Workflows in CivicStream are created programmatically in Python code, NOT through API calls. The proper workflow creation process is:

1. **Define workflows in Python** using the Airflow-inspired syntax in `/app/workflows/examples/`
2. **Auto-register** them through the registry system (`/app/workflows/registry.py`)
3. **Persist to database** via the workflow service layer
4. **Access via API** for runtime operations (instances, execution, monitoring)

Example workflow creation:
```python
def create_my_workflow() -> Workflow:
    workflow = Workflow("my_workflow_id", "My Workflow", "Description")
    
    step_a = ActionStep("step_a", "First Step", action=my_function)
    step_b = ApprovalStep("step_b", "Approval", approvers=["manager"])
    
    step_a >> step_b  # Define flow
    
    workflow.add_step(step_a)
    workflow.add_step(step_b)
    workflow.set_start(step_a)
    
    return workflow
```

The API endpoints are for managing workflow instances and execution, not for creating new workflow definitions.

### Development Standards
**IMPORTANT**: Never cut corners by adding fake, mock, or test data to make features appear to work. Always fix the underlying issues properly:

1. **No Mock Data** - Don't create fake database entries or API responses to simulate functionality
2. **No Shortcuts** - Don't add temporary hacks or workarounds that bypass the proper architecture
3. **Fix Root Causes** - Always identify and resolve the actual underlying problems
4. **Proper Integration** - Ensure all components work together as designed, not through artificial connections

Example: If instance tracking shows 0% progress, don't add fake step data - instead fix the workflow-to-database synchronization issue.

### Resume Instructions
To continue after restart, say:
"Continue working on the CivicStream project. We were adding the remaining issues to the kanban boards. Frontend issues 9 and 10 still need to be created for Mobile-Responsive Views and Bulk Operations."