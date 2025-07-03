# Claude Code Instructions for CivicStream

## Project Overview
CivicStream is a process automation platform that helps organizations streamline workflows using Directed Acyclic Graphs (DAGs). It enables visual process modeling through Mermaid diagrams and other visualization tools, following DRY principles where each node executes a single, reusable function.

## Architecture Overview

### Core Components
1. **Backend API** (FastAPI)
   - RESTful API for process management
   - DAG execution engine
   - MongoDB integration for instance data persistence
   - Process validation and conditional routing

2. **Citizen Frontend** (Web)
   - Portal for users to initiate process instances
   - Process status tracking
   - Document upload/submission interface

3. **Mobile Apps** (iOS/Android)
   - Native mobile experience for citizens
   - Push notifications for process updates

4. **Backoffice/Admin Portal**
   - DAG visual editor using Mermaid
   - Process instance monitoring
   - Data validation and approval workflows
   - User and permission management

5. **Blockchain Integration**
   - Process completion immortalization
   - Audit trail and transparency

### Technology Stack
- **Backend**: Python with FastAPI
- **Database**: MongoDB for process instances
- **Frontend**: [To be determined - React/Vue/Angular]
- **Mobile**: [To be determined - React Native/Flutter/Native]
- **Container Platform**: Docker + Kubernetes
- **Cloud**: Azure (AKS, Azure Functions, Cosmos DB)
- **CI/CD**: Azure DevOps or GitHub Actions
- **Testing**: Pytest (backend), Jest/Cypress (frontend)
- **Monitoring**: Azure Monitor, Application Insights

### DAG System Design

#### Terminology
- **Workflow**: The complete process definition (formerly DAG)
- **Step**: Individual executable unit (formerly Node)
- **Instance**: A single execution of a workflow initiated by a user
- **Transition**: Connection between steps with optional conditions

#### Step Types (via inheritance)
```python
class BaseStep:
    - id: str
    - name: str
    - inputs: Dict[str, Any]
    - outputs: Dict[str, Any]
    - validations: List[Validation]

class ActionStep(BaseStep):
    - execute(): Result

class ConditionalStep(BaseStep):
    - conditions: List[Condition]
    - evaluate(): NextStep

class ApprovalStep(BaseStep):
    - approvers: List[User]
    - approval_type: ApprovalType

class IntegrationStep(BaseStep):
    - service: ExternalService
    - endpoint: str
```

#### Workflow Definition Style
```python
# Airflow-inspired syntax
workflow = Workflow("citizen_registration")
step_a = ActionStep("validate_identity")
step_b = ActionStep("check_duplicates")
step_c = ConditionalStep("age_verification")
step_d = ActionStep("create_account")
step_e = ActionStep("send_notification")
approval = ApprovalStep("manager_approval")

# Define flow
step_a >> step_b >> step_c
step_c.when(lambda: age >= 18) >> approval >> step_d
step_c.when(lambda: age < 18) >> step_e
```

### Repository Structure (Microservices)
```
civicstream-backend/
civicstream-frontend-citizen/
civicstream-frontend-admin/
civicstream-mobile-ios/
civicstream-mobile-android/
civicstream-shared-libs/
civicstream-infrastructure/
```

### Development Guidelines

#### Code Style
- Python: Follow PEP 8, use Black formatter
- JavaScript/TypeScript: ESLint + Prettier
- Use type hints in Python, TypeScript for frontend
- DRY principle: Each step should be a single, reusable function
- SOLID principles for class design

#### Testing Strategy
- Unit tests for all step implementations
- Integration tests for workflow execution
- API endpoint tests with FastAPI TestClient
- Frontend component tests
- E2E tests for critical user journeys
- Minimum 80% code coverage

#### Git Workflow
- Main branch: `main` (production-ready)
- Development branch: `develop`
- Feature branches: `feature/description`
- Hotfix branches: `hotfix/description`
- Pull requests required for all merges
- Commit message format: `type(scope): description`

#### Security Considerations
- OAuth2/JWT for authentication
- Role-based access control (RBAC)
- Data encryption at rest and in transit
- Input validation at every step
- Audit logging for all actions
- GDPR compliance for citizen data

### Common Commands

#### Backend Development
```bash
# Install dependencies
pip install -r requirements.txt

# Run tests
pytest tests/ -v --cov=app

# Run linting
black app/ tests/
flake8 app/ tests/
mypy app/

# Start development server
uvicorn app.main:app --reload

# Build Docker image
docker build -t civicstream-backend:latest .
```

#### Frontend Development
```bash
# Install dependencies
npm install

# Run tests
npm test
npm run test:e2e

# Run linting
npm run lint
npm run type-check

# Start development server
npm run dev

# Build for production
npm run build
```

#### Infrastructure
```bash
# Deploy to Azure
az aks get-credentials --resource-group civicstream-rg --name civicstream-aks
kubectl apply -f k8s/

# Run migrations
python manage.py migrate

# Seed test data
python manage.py seed
```

### Environment Variables
```
# Backend
MONGODB_URI=mongodb://...
AZURE_STORAGE_CONNECTION=...
JWT_SECRET=...
BLOCKCHAIN_API_KEY=...

# Frontend
REACT_APP_API_URL=...
REACT_APP_AUTH_URL=...
```

### Important Notes
- Always validate step inputs/outputs against defined schemas
- Ensure backward compatibility when modifying workflows
- Document all external service integrations
- Consider performance implications for large workflows
- Implement proper error handling and retry mechanisms
- Use feature flags for gradual rollouts
- Monitor workflow execution times and optimize bottlenecks