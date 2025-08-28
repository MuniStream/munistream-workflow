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

from ...models.customer import Customer, CustomerStatus
from ...core.config import settings

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
    Get current authenticated customer from JWT token.
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
    
    try:
        payload = jwt.decode(token, CUSTOMER_SECRET_KEY, algorithms=[ALGORITHM])
        
        # Verify this is a customer token
        if payload.get("type") != "customer":
            raise credentials_exception
        
        customer_id: str = payload.get("sub")
        if customer_id is None:
            raise credentials_exception
            
    except JWTError:
        raise credentials_exception
    
    # Get customer from database
    customer = await Customer.get(customer_id)
    if customer is None:
        raise credentials_exception
    
    # Check if customer is active
    if customer.status != CustomerStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Customer account is not active"
        )
    
    return customer


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


@router.get("/workflows/my-instances")
async def get_customer_instances(
    current_customer: Customer = Depends(get_current_customer)
):
    """Get all workflow instances for the current customer"""
    from ...models.workflow import WorkflowInstance
    
    # Find all instances for this customer
    instances = await WorkflowInstance.find(
        WorkflowInstance.user_id == str(current_customer.id)
    ).sort(-WorkflowInstance.created_at).to_list()
    
    return {
        "instances": [
            {
                "instance_id": inst.instance_id,
                "workflow_id": inst.workflow_id,
                "status": inst.status,
                "current_step": inst.current_step,
                "created_at": inst.created_at,
                "updated_at": inst.updated_at,
                "context": inst.context
            }
            for inst in instances
        ],
        "total": len(instances)
    }