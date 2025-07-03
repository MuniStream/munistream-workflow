# CivicStream Backend

FastAPI-based backend service for the CivicStream workflow automation platform.

## Features

- Workflow execution engine with DAG support
- Multiple step types: Action, Conditional, Approval, Integration, Terminal
- MongoDB integration for process instance persistence
- RESTful API for workflow management
- Async execution support
- Mermaid diagram generation

## Setup

1. Create virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set up environment variables:
```bash
cp .env.example .env
# Edit .env with your configuration
```

4. Run the application:
```bash
uvicorn app.main:app --reload
```

## Project Structure

```
backend/
├── app/
│   ├── api/           # API endpoints
│   ├── core/          # Core configuration
│   ├── models/        # Database models
│   ├── services/      # Business logic
│   ├── workflows/     # Workflow engine
│   │   ├── base.py    # Base step classes
│   │   ├── workflow.py # Workflow orchestration
│   │   └── examples/  # Example workflows
│   └── utils/         # Utilities
├── tests/             # Test suite
├── docs/              # Documentation
└── scripts/           # Utility scripts
```

## Example Workflow

See `app/workflows/examples/citizen_registration.py` for a complete example of:
- Identity validation
- Duplicate checking
- Age-based routing
- Approval processes
- Terminal states (SUCCESS, FAILURE, REJECTED, PENDING)

## API Documentation

Once running, visit:
- Swagger UI: http://localhost:8000/api/v1/docs
- ReDoc: http://localhost:8000/api/v1/redoc

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app tests/

# Run specific test file
pytest tests/test_workflows.py
```

## Development

```bash
# Format code
black app/ tests/

# Lint
flake8 app/ tests/

# Type checking
mypy app/
```