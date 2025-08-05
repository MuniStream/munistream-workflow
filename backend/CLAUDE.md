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

### Current Task (Last Updated: 2025-07-14)
✅ COMPLETED: Comprehensive citizen data collection system implementation
- Implemented workflow execution pause/resume for citizen input steps
- Added plugin system for external workflows (Aquabilidad integration)
- Created citizen data submission APIs with validation and file upload
- Added internationalization support (English/Spanish)
- Updated database models for citizen input forms and workflow management

### Tomorrow's Priorities
1. 🔴 HIGH: Add Citizen Instance Validation & Approval APIs (Backend Issue #14)
   - Create admin endpoints for validating citizen submitted data
   - Add approve/reject functionality with audit logging
   - Implement role-based permissions for admin actions
2. 🔴 HIGH: Add Real-time Notifications & WebSocket Integration (Backend Issue #1)
3. 🟡 MEDIUM: Create User Management Interface (Backend Issue #3)

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

### API Endpoints
**CRITICAL**: All API endpoints use `/api/v1/` prefix. This is VERY commonly forgotten!

**Correct API endpoint format:**
- ❌ WRONG: `http://localhost:8000/api/instances/`
- ✅ CORRECT: `http://localhost:8000/api/v1/instances/`

**Common API endpoints:**
- `POST /api/v1/instances/` - Create workflow instance
- `GET /api/v1/instances/` - List workflow instances  
- `GET /api/v1/instances/{instance_id}` - Get specific instance
- `POST /api/v1/instances/{instance_id}/validate-citizen-data` - Validate citizen data
- `GET /api/v1/workflows/` - List available workflows
- `GET /api/v1/workflows/catalog` - Get public workflow catalog

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
**CRITICAL**: NEVER NEVER NEVER EVER add mock data, fake data, or temporary data under any circumstances. Always fix the underlying issues properly:

1. **ABSOLUTELY NO MOCK DATA** - NEVER EVER create fake database entries, mock API responses, temporary data, or simulated functionality. This is strictly forbidden and must never be done under any circumstances
2. **No Shortcuts** - Don't add temporary hacks or workarounds that bypass the proper architecture
3. **Fix Root Causes** - Always identify and resolve the actual underlying problems
4. **Proper Integration** - Ensure all components work together as designed, not through artificial connections
5. **No Claude References** - Never mention Claude, Claude Code, or AI assistance in commit messages, code comments, documentation, or anywhere in the codebase
6. **Test Before Commit** - ALWAYS test and demonstrate that everything works completely before pushing or committing code. This includes:
   - Running the application and verifying it starts successfully
   - Testing core functionality works as expected
   - Verifying Docker containers build and run properly
   - Confirming all services are accessible at expected URLs
   - No commits should be made without thorough testing first
7. **Use Docker Containers** - ALWAYS use Docker containers for running services, never run services directly. Use `docker-compose up` or individual Docker commands, not direct npm/python commands
8. **No Hardcoded Paths** - NEVER hardcode local file paths, URLs, or environment-specific values in configuration files or code. Always use proper Git repositories, environment variables, or configuration mechanisms that work across different environments
9. **Never Commit CLAUDE.md** - NEVER commit CLAUDE.md files to any repository. These are local development context files and should remain local only

**MOCK DATA IS STRICTLY PROHIBITED**: If data is missing, create proper backend endpoints, database seeding, or real data population mechanisms. Never use mock, fake, or temporary data as a solution.

**HARDCODING IS STRICTLY PROHIBITED**: Never hardcode file paths, local URLs, or environment-specific configurations. Use proper Git workflows - commit and push changes to repositories so they can be pulled and synced properly.

Example: If instance tracking shows 0% progress, don't add fake step data - instead fix the workflow-to-database synchronization issue.

### Resume Instructions
To continue after restart, say:
"Continue working on the CivicStream project. We were adding the remaining issues to the kanban boards. Frontend issues 9 and 10 still need to be created for Mobile-Responsive Views and Bulk Operations."