# 🏛️ MuniStream

**A DAG-based workflow automation platform for government processes**

MuniStream empowers government organizations to model, execute, and optimize citizen-facing processes using Directed Acyclic Graphs (DAGs). Built with modern microservices architecture, it provides transparency, efficiency, and accountability in government operations.

## 🚀 Features

### Core Capabilities
- **📊 DAG-based Workflow Engine** - Model complex government processes as directed acyclic graphs
- **⚡ Performance Monitoring** - Real-time bottleneck detection and citizen wait time analysis
- **🔄 Step-by-Step Execution** - Independent, reusable workflow components following DRY principles
- **🎯 Conditional Routing** - Smart decision-making based on citizen data and context
- **✅ Approval Workflows** - Built-in approval processes with multiple approver types
- **🔗 External Integrations** - Connect with existing government systems and services
- **🔒 Blockchain Integration** - Immutable process records (planned)

### Step Types
- **ActionStep** - Execute specific business logic
- **ConditionalStep** - Route based on conditions
- **ApprovalStep** - Human approval with configurable approvers
- **IntegrationStep** - External service calls
- **TerminalStep** - Workflow completion with status

### Performance Analytics
- **📈 Real-time Metrics** - Execution times, success rates, error tracking
- **🎯 Bottleneck Detection** - Identify delays in approval processes and external services
- **📊 Citizen Wait Time Analysis** - Track time between workflow steps
- **💡 Optimization Suggestions** - AI-powered recommendations for process improvement

## 🏗️ Architecture

```
MuniStream Platform
├── 🖥️  Backend (FastAPI)           # Workflow engine and APIs
├── 👤 Citizen Frontend (React)     # Citizen-facing interface
├── 📱 Mobile Apps (React Native)   # iOS/Android applications  
├── 🏢 Backoffice Admin (React)     # Administrative dashboard
└── ⛓️  Blockchain Layer (Future)    # Immutable process records
```

## 📋 Example Workflows

### 🆔 Citizen Registration
Complete citizen onboarding with age-based routing:
- Identity verification
- Duplicate checking
- Age-based approval routing (adult vs minor)
- Account creation
- Welcome notifications
- Blockchain recording

### 🏢 Business License Application
Streamlined business registration process:
- Application validation
- Document verification
- Fee calculation and payment
- Multi-level approval
- License generation
- Notification system

### 📝 Permit Renewal
Automated permit renewal with compliance checking:
- Eligibility verification
- Compliance audit
- Fee processing
- Approval workflow
- Permit issuance

### 📞 Complaint Handling
Citizen complaint resolution system:
- Complaint classification
- Department routing
- Investigation workflow
- Resolution tracking
- Citizen feedback

## 🛠️ Technology Stack

### Backend
- **FastAPI** - High-performance Python web framework
- **MongoDB** - Document database with Beanie ODM
- **NetworkX** - Graph processing for DAG operations
- **Pydantic** - Data validation and serialization
- **AsyncIO** - Asynchronous processing

### Infrastructure  
- **Docker** - Containerization
- **Docker Compose** - Multi-service orchestration
- **Nginx** - Reverse proxy and load balancing
- **MongoDB** - Primary database

### Monitoring & Observability
- **Prometheus** - Metrics collection
- **OpenTelemetry** - Distributed tracing
- **Custom Performance Analytics** - Bottleneck detection

## 🚀 Quick Start

### Prerequisites
- Docker and Docker Compose
- Python 3.11+
- Git

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/MuniStream/munistream-workflow.git
   cd munistream-workflow
   ```

2. **Start the development environment**
   ```bash
   cd backend
   docker-compose up -d
   ```

3. **Access the API**
   - API Documentation: http://localhost:8000/docs
   - Health Check: http://localhost:8000/health
   - Performance Monitoring: http://localhost:8000/api/v1/performance/health

### Development Setup

1. **Install dependencies**
   ```bash
   cd backend
   pip install -r requirements.txt
   ```

2. **Set up environment**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

3. **Run locally**
   ```bash
   uvicorn app.main:app --reload
   ```

## 📊 Performance Monitoring

CivicStream includes comprehensive performance monitoring to help administrators optimize citizen experiences:

### Available Metrics
- Step execution times
- Queue wait times  
- Success/failure rates
- Memory usage
- Bottleneck identification

### API Endpoints
```bash
# List all workflows
GET /api/v1/performance/workflows

# Get step performance metrics
GET /api/v1/performance/steps/{step_id}/metrics

# Analyze workflow bottlenecks
GET /api/v1/performance/workflows/{workflow_id}/bottlenecks

# Execute steps manually
POST /api/v1/performance/steps/execute

# Validate step inputs
POST /api/v1/performance/steps/validate
```

### Demo Script
```bash
python test_performance_monitoring.py
```

## 📚 Documentation

- **[Performance Monitoring](backend/PERFORMANCE_MONITORING.md)** - Comprehensive guide to performance analytics
- **[Development Guide](CLAUDE.md)** - Development instructions and architecture
- **[API Documentation](http://localhost:8000/docs)** - Interactive API documentation (when running)

## 🧪 Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app

# Run specific test suite
pytest tests/api/ -v
```

## 🐳 Docker Deployment

### Development
```bash
docker-compose -f docker-compose.dev.yml up -d
```

### Production
```bash
docker-compose up -d
```

### Environment Variables
Key configuration options in `.env`:
- `MONGODB_URL` - Database connection string
- `SECRET_KEY` - Application secret key
- `BACKEND_CORS_ORIGINS` - Allowed CORS origins

## 🔄 Workflow Definition Example

```python
from app.workflows.base import ActionStep, ConditionalStep, TerminalStep
from app.workflows.workflow import Workflow

# Create workflow
workflow = Workflow(
    workflow_id="simple_approval",
    name="Simple Approval Process"
)

# Define steps
validate = ActionStep("validate", "Validate Request", validate_request)
approve = ApprovalStep("approve", "Manager Approval", ["manager"])
success = TerminalStep("success", "Approved", "SUCCESS")
rejected = TerminalStep("rejected", "Rejected", "REJECTED")

# Define flow
validate >> approve
approve.when(is_approved) >> success
approve.when(is_rejected) >> rejected

# Add to workflow
workflow.add_steps([validate, approve, success, rejected])
workflow.set_start(validate)
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🏛️ Government Focus

MuniStream is specifically designed for government organizations with features like:
- **Transparency** - Full audit trails and process visibility
- **Compliance** - Built-in approval workflows and documentation
- **Efficiency** - Performance monitoring and bottleneck elimination
- **Accessibility** - Multi-channel citizen engagement (web, mobile, in-person)
- **Accountability** - Immutable process records via blockchain

## 📞 Support

- **Documentation**: [View Docs](./docs/)
- **Issues**: [GitHub Issues](https://github.com/MuniStream/munistream-workflow/issues)
- **Discussions**: [GitHub Discussions](https://github.com/MuniStream/munistream-workflow/discussions)

---

**Built with ❤️ for better government services**# Test

## Code language: English

Code shared by every tenant is written in **English**: identifiers, comments,
docstrings, log messages and test names. This applies to this repository.

What citizens and staff read stays in their language, but it lives outside the
code: i18n files, notification templates and user-facing messages (UI text,
API error details shown to users).

Tenant-specific code (`tenants/<id>/` and each tenant's workflow repository)
may follow the tenant's own language. Existing Spanish code in shared
repositories is migrated as it is touched, not in bulk.
