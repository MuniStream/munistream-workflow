"""
Public authentication endpoints for citizen portal.
Separate from admin authentication.
"""

from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, status, Header, Depends
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
import logging

from ...models.customer import Customer, CustomerStatus
from ...core.config import settings

logger = logging.getLogger(__name__)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT settings - using different secret for customer tokens
CUSTOMER_SECRET_KEY = settings.SECRET_KEY + "_CUSTOMER"  # Different from admin secret
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours for customers

router = APIRouter()


class CustomerLoginRequest(BaseModel):
    """Customer login request schema"""
    email: EmailStr
    password: str


class CustomerRegisterRequest(BaseModel):
    """Customer registration request schema"""
    email: EmailStr
    password: str
    full_name: str
    phone: Optional[str] = None
    document_number: Optional[str] = None


class CustomerAuthResponse(BaseModel):
    """Customer authentication response"""
    access_token: str
    token_type: str = "bearer"
    customer: Dict[str, Any]


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against hash"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password"""
    return pwd_context.hash(password)


def create_customer_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token for customer"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire, "type": "customer"})  # Mark as customer token
    encoded_jwt = jwt.encode(to_encode, CUSTOMER_SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


async def get_current_customer(authorization: Optional[str] = Header(None)) -> Customer:
    """
    Get current authenticated customer from Keycloak token.
    Uses manual header extraction to avoid conflicts with admin OAuth2.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Extract token from "Bearer <token>" format
    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication scheme",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format",
            headers={"WWW-Authenticate": "Bearer"},
        )

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Use Keycloak to validate the token
    from ...auth.provider import keycloak
    try:
        payload = await keycloak.verify_token(token)

        # Get user ID from Keycloak token
        customer_id: str = payload.get("sub")
        if customer_id is None:
            raise credentials_exception

    except Exception as e:
        logger.error(f"Token verification failed: {e}")
        raise credentials_exception

    # Get or create customer from Keycloak user info
    # Use email as the unique identifier to find existing customers
    email = payload.get("email", "")
    if not email:
        raise credentials_exception

    customer = await Customer.find_one(Customer.email == email)
    if customer is None:
        # Create customer from Keycloak token if doesn't exist
        username = payload.get("preferred_username", email)
        name = payload.get("name", username)

        customer = Customer(
            # Don't set id - let MongoDB generate it
            email=email,
            full_name=name,
            phone=payload.get("phone", ""),
            document_number="",
            password_hash="",  # No password needed with Keycloak
            status=CustomerStatus.ACTIVE,
            email_verified=payload.get("email_verified", False),
            keycloak_id=customer_id  # Store Keycloak ID separately
        )
        await customer.save()

    return customer


async def get_current_customer_optional(authorization: Optional[str] = Header(None)) -> Optional[Customer]:
    """Optional customer authentication - wrapper that catches exceptions and returns None"""
    try:
        # If no authorization header, return None immediately
        if not authorization:
            return None
        return await get_current_customer(authorization)
    except Exception as e:
        logger.debug(f"Optional authentication failed: {e}")
        return None


@router.post("/auth/register", response_model=CustomerAuthResponse)
async def register_customer(request: CustomerRegisterRequest):
    """Register a new customer for the citizen portal"""
    # Check if customer already exists
    existing = await Customer.find_one(Customer.email == request.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Create new customer
    customer = Customer(
        email=request.email,
        password_hash=get_password_hash(request.password),
        full_name=request.full_name,
        phone=request.phone,
        document_number=request.document_number,
        status=CustomerStatus.ACTIVE,
        email_verified=False  # In production, require email verification
    )
    
    await customer.create()
    
    # Create access token
    access_token = create_customer_access_token(
        data={
            "sub": str(customer.id),
            "email": customer.email,
            "full_name": customer.full_name
        }
    )
    
    return CustomerAuthResponse(
        access_token=access_token,
        token_type="bearer",
        customer=customer.to_public_dict()
    )


@router.post("/auth/login", response_model=CustomerAuthResponse)
async def login_customer(request: CustomerLoginRequest):
    """Login customer to citizen portal"""
    # Find customer by email
    customer = await Customer.find_one(Customer.email == request.email)
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Verify password
    if not verify_password(request.password, customer.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Check if customer is active
    if customer.status != CustomerStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is not active"
        )
    
    # Update last login
    customer.update_last_login()
    await customer.save()
    
    # Create access token
    access_token = create_customer_access_token(
        data={
            "sub": str(customer.id),
            "email": customer.email,
            "full_name": customer.full_name
        }
    )
    
    return CustomerAuthResponse(
        access_token=access_token,
        token_type="bearer",
        customer=customer.to_public_dict()
    )


@router.get("/auth/me")
async def get_current_customer_profile(current_customer: Customer = Depends(get_current_customer)):
    """Get current customer profile"""
    return current_customer.to_public_dict()


@router.post("/workflows/{workflow_id}/start")
async def start_citizen_workflow(
    workflow_id: str,
    initial_data: Optional[Dict[str, Any]] = None,
    current_customer: Customer = Depends(get_current_customer)
):
    """Start a workflow as an authenticated citizen"""
    from ...services.workflow_service import workflow_service
    
    initial_context = initial_data or {}
    
    # Add customer info to context
    initial_context["customer_id"] = str(current_customer.id)
    initial_context["customer_email"] = current_customer.email
    initial_context["customer_name"] = current_customer.full_name
    initial_context["user_id"] = str(current_customer.id)  # Add user_id for EntityRequirementOperator
    
    try:
        # Create DAG instance with customer as user
        dag_instance = await workflow_service.create_instance(
            workflow_id=workflow_id,
            user_id=str(current_customer.id),  # Use customer ID as user ID
            initial_data=initial_context
        )
        
        # Start execution
        await workflow_service.execute_instance(dag_instance.instance_id)
        
        return {
            "success": True,
            "instance_id": dag_instance.instance_id,
            "workflow_id": dag_instance.dag.dag_id,
            "status": dag_instance.status.value,
            "message": "Workflow started successfully"
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/track/{instance_id}")
async def track_instance(
    instance_id: str,
    current_customer: Customer = Depends(get_current_customer)
):
    """
    Track workflow instance status.
    Returns current state and any required actions.
    Requires authentication - only authenticated users can track instances.
    """
    from ...models.workflow import WorkflowInstance
    from ...services.workflow_service import workflow_service

    # Get database instance
    db_instance = await WorkflowInstance.find_one(
        WorkflowInstance.instance_id == instance_id
    )
    if not db_instance:
        raise HTTPException(status_code=404, detail="Instance not found")

    # Get DAG instance for detailed state
    dag_instance = await workflow_service.get_instance(instance_id)

    # Check if waiting for input - Universal form concatenation approach
    requires_input = False
    input_form = {}
    waiting_tasks = []
    waiting_for = None

    if dag_instance:
        # Check if instance is paused and waiting for input
        if db_instance.status == "paused" and dag_instance.context.get("waiting_for"):
            requires_input = True
            waiting_for = dag_instance.context.get("waiting_for")
            if dag_instance.context.get("form_config"):
                input_form.update(dag_instance.context["form_config"])

        for task_id, state in dag_instance.task_states.items():
            if state.get("status") == "waiting":
                requires_input = True
                waiting_tasks.append(task_id)

                task = dag_instance.dag.tasks.get(task_id)
                task_form = {}

                # Merge from ANY source - output_data first (async operators like EntityPickerOperator)
                if state.get("output_data", {}).get("form_config"):
                    task_form.update(state["output_data"]["form_config"])
                else:
                    # Try to get form_config from global context as fallback
                    if dag_instance.context.get("form_config"):
                        task_form.update(dag_instance.context["form_config"])

                # Then merge from task.form_config (sync operators like UserInputOperator)
                if task and hasattr(task, 'form_config'):
                    task_form.update(task.form_config)

                # Add task-specific metadata
                if task_form:  # Only add metadata if we found a form
                    task_form["current_step_id"] = task_id
                    task_waiting_for = state.get("output_data", {}).get("waiting_for")
                    if task_waiting_for:
                        task_form["waiting_for"] = task_waiting_for
                        waiting_for = task_waiting_for  # Set top-level waiting_for

                    # Merge into global input_form
                    input_form.update(task_form)

        # Handle multiple waiting tasks
        if len(waiting_tasks) > 1:
            input_form["multiple_tasks"] = waiting_tasks
        elif len(waiting_tasks) == 1:
            # For single task, ensure current_step_id is set
            if "current_step_id" not in input_form:
                input_form["current_step_id"] = waiting_tasks[0]

    # Calculate progress
    total_steps = len(dag_instance.dag.tasks) if dag_instance and dag_instance.dag else 0
    completed_steps = 0
    step_progress = []

    if dag_instance:
        for task_id, state in dag_instance.task_states.items():
            status_val = state.get("status", "pending")
            if status_val == "completed":
                completed_steps += 1

            step_progress.append({
                "step_id": task_id,
                "name": task_id.replace("_", " ").title(),
                "description": f"Step {task_id}",
                "status": status_val,
                "started_at": state.get("started_at"),
                "completed_at": state.get("completed_at")
            })

    progress_percentage = (completed_steps / total_steps * 100) if total_steps > 0 else 0

    # Get workflow info
    workflow = await workflow_service.get_workflow_definition(db_instance.workflow_id)
    workflow_name = workflow.name if workflow else db_instance.workflow_id

    return {
        "instance_id": instance_id,
        "workflow_id": db_instance.workflow_id,
        "workflow_name": workflow_name,
        "status": db_instance.status,
        "progress_percentage": progress_percentage,
        "current_step": db_instance.current_step,
        "created_at": db_instance.created_at.isoformat() if db_instance.created_at else None,
        "updated_at": db_instance.updated_at.isoformat() if db_instance.updated_at else None,
        "completed_at": db_instance.completed_at.isoformat() if db_instance.completed_at else None,
        "total_steps": total_steps,
        "completed_steps": completed_steps,
        "step_progress": step_progress,
        "requires_input": requires_input,
        "input_form": input_form,
        "waiting_for": waiting_for,
        "estimated_completion": None,  # Could calculate based on average step time
        "message": f"Workflow {db_instance.status}"
    }


@router.get("/workflows/my-instances")
async def get_customer_instances(
    current_customer: Customer = Depends(get_current_customer)
):
    """Get all workflow instances for the current customer (excluding ADMIN workflows)"""
    from ...models.workflow import WorkflowInstance, WorkflowType

    # Find all instances for this customer, excluding ADMIN workflows
    instances = await WorkflowInstance.find(
        WorkflowInstance.user_id == str(current_customer.id),
        WorkflowInstance.workflow_type != WorkflowType.ADMIN
    ).sort(-WorkflowInstance.created_at).to_list()

    # Get workflow definitions for names
    from ...services.workflow_service import workflow_service

    return {
        "instances": [
            {
                "instance_id": inst.instance_id,
                "workflow_id": inst.workflow_id,
                "workflow_name": getattr(await workflow_service.get_workflow_definition(inst.workflow_id), 'name', inst.workflow_id) if inst.workflow_id else inst.workflow_id,
                "workflow_type": inst.workflow_type,
                "status": inst.status,
                "current_step": inst.current_step,
                "created_at": inst.created_at,
                "updated_at": inst.updated_at,
                "completed_at": inst.completed_at,
                # Only include essential context data, not the full context object
                "progress_percentage": _calculate_progress_percentage(inst)
            }
            for inst in instances
        ],
        "total": len(instances)
    }


def _calculate_progress_percentage(instance) -> float:
    """Calculate progress percentage for an instance"""
    try:
        if instance.status == "completed":
            return 100.0
        elif instance.status in ["failed", "cancelled"]:
            return 0.0

        # Try to get progress from context if available
        if hasattr(instance, 'context') and instance.context:
            if 'progress_percentage' in instance.context:
                return float(instance.context.get('progress_percentage', 0))

            # Calculate based on completed steps if available
            total_steps = instance.context.get('total_steps', 0)
            completed_steps = instance.context.get('completed_steps', 0)
            if total_steps > 0:
                return (completed_steps / total_steps) * 100.0

        # Default fallback based on status
        if instance.status in ["running", "in_progress"]:
            return 50.0
        elif instance.status in ["waiting", "paused"]:
            return 25.0

        return 0.0
    except:
        return 0.0