"""
Test Configuration and Fixtures - Real API Testing
All fixtures connect to real services - no mocking
"""

import os
import asyncio
import logging
import pytest
from dotenv import load_dotenv
from typing import AsyncGenerator

from .auth_helper import RealKeycloakAuth, AuthTokens
from .api_client import RealApiClient


# Load test environment variables
test_env_path = os.path.join(os.path.dirname(__file__), "test.env")
if os.path.exists(test_env_path):
    load_dotenv(test_env_path)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def auth_client() -> AsyncGenerator[RealKeycloakAuth, None]:
    """Real Keycloak authentication client fixture"""
    client = RealKeycloakAuth()
    yield client


@pytest.fixture(scope="session")
async def api_client() -> AsyncGenerator[RealApiClient, None]:
    """Real API client fixture"""
    client = RealApiClient()

    # Check API health before running tests
    if not await client.health_check():
        pytest.skip("API is not accessible - check if backend is running")

    yield client

    # Generate redirect report after all tests
    redirect_report = client.generate_redirect_report()
    if redirect_report:
        print("\n" + redirect_report)


@pytest.fixture(scope="session")
async def user_tokens(auth_client: RealKeycloakAuth) -> AsyncGenerator[AuthTokens, None]:
    """Real user authentication tokens"""
    try:
        tokens = await auth_client.authenticate_user()
        yield tokens
    except Exception as e:
        pytest.skip(f"Failed to authenticate user: {e}")


@pytest.fixture(scope="session")
async def admin_tokens(auth_client: RealKeycloakAuth) -> AsyncGenerator[AuthTokens, None]:
    """Real admin authentication tokens"""
    try:
        tokens = await auth_client.authenticate_admin()
        yield tokens
    except Exception as e:
        pytest.skip(f"Failed to authenticate admin: {e}")


@pytest.fixture
async def clean_test_data():
    """Fixture to clean up test data after each test"""
    # Setup: nothing to do
    yield

    # Cleanup: remove any test data created during the test
    cleanup_enabled = os.getenv("TEST_CLEANUP_DATA", "true").lower() == "true"
    if cleanup_enabled:
        # TODO: Implement cleanup logic for test data
        # This would connect to the real database and remove test entries
        pass


@pytest.fixture
def test_workflow_data():
    """Sample workflow data for testing"""
    return {
        "workflow_id": "test_workflow_001",
        "name": "Test Workflow",
        "description": "Test workflow for API testing",
        "version": "1.0.0"
    }


@pytest.fixture
def test_instance_data():
    """Sample instance data for testing"""
    return {
        "workflow_id": "test_workflow_001",
        "context": {
            "test_field": "test_value",
            "test_number": 123
        }
    }


@pytest.fixture
def test_document_data():
    """Sample document data for testing"""
    return {
        "name": "test_document.pdf",
        "description": "Test document for API testing",
        "category": "test"
    }


# Parametrized fixtures for testing different endpoints
@pytest.fixture(params=[
    "/workflows",
    "/instances",
    "/documents",
    "/admin/pending-approvals",
    "/performance/stats",
    "/entities/types",
    "/categories",
    "/teams",
    "/themes/current",
    "/plugins"
])
def endpoint_path(request):
    """Parametrized fixture for testing different API endpoints"""
    return request.param


@pytest.fixture(params=["GET", "POST", "PUT", "DELETE"])
def http_method(request):
    """Parametrized fixture for testing different HTTP methods"""
    return request.param


def pytest_configure(config):
    """Configure pytest with custom markers and settings"""
    # Load test.env file if environment variables are not set
    test_env_path = os.path.join(os.path.dirname(__file__), "test.env")
    if os.path.exists(test_env_path):
        load_dotenv(test_env_path)

    # Check required environment variables
    required_vars = [
        "TEST_API_BASE_URL",
        "TEST_KEYCLOAK_URL",
        "TEST_KEYCLOAK_REALM",
        "TEST_USER_USERNAME",
        "TEST_USER_PASSWORD",
        "TEST_ADMIN_USERNAME",
        "TEST_ADMIN_PASSWORD"
    ]

    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        print(f"⚠️ Missing environment variables: {', '.join(missing_vars)}")
        print("ℹ️ Tests will be skipped - ensure test.env is properly configured")
        # Don't raise error, let tests skip gracefully


def pytest_collection_modifyitems(config, items):
    """Modify test collection to add markers based on test names"""
    for item in items:
        # Add markers based on test file names
        if "auth" in item.nodeid:
            item.add_marker(pytest.mark.auth)
        elif "public" in item.nodeid:
            item.add_marker(pytest.mark.public)
        elif "admin" in item.nodeid:
            item.add_marker(pytest.mark.admin)
        elif "workflow" in item.nodeid:
            item.add_marker(pytest.mark.workflows)
        elif "instance" in item.nodeid:
            item.add_marker(pytest.mark.instances)
        elif "document" in item.nodeid:
            item.add_marker(pytest.mark.documents)
        elif "performance" in item.nodeid:
            item.add_marker(pytest.mark.performance)
        elif "redirect" in item.nodeid:
            item.add_marker(pytest.mark.redirect)


def pytest_sessionfinish(session, exitstatus):
    """Actions to perform after test session ends"""
    if hasattr(session, "api_client"):
        # Final redirect report would be generated by api_client fixture
        pass