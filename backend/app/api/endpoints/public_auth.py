"""
Public authentication endpoints for citizen portal.
Separate from admin authentication.
"""

from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import json
from fastapi import APIRouter, HTTPException, status, Header, Depends
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
import logging

from ...models.customer import Customer, CustomerStatus
from ...core.config import settings
from ...core.i18n import t as translate
from ...auth.auth_callbacks import run_post_auth_callbacks

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


def _parse_personas_morales(raw: Any) -> List[Dict[str, Any]]:
    """
    Normalize the `personas_morales` token claim into a list of dicts.

    The shim encodes the personas morales list as a JSON string so it survives
    the OIDC -> Keycloak -> backend hop. Keycloak may also deliver it already
    parsed (list) depending on the mapper's jsonType. Be tolerant of both and
    never raise on malformed input (a citizen without a persona moral logs in
    fine with an empty list).
    """
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            logger.warning("Could not parse personas_morales claim as JSON")
            return []
    return []


def _sync_llavemx_profile(customer: Customer, payload: Dict[str, Any]) -> None:
    """
    Sync Llave MX identity data from the Keycloak token claims into the Customer.

    Called on every authenticated request so the MuniStream profile mirrors the
    FORCE-synced Keycloak attributes (persona física + all associated personas
    morales). Only overwrites fields when the corresponding claim is present.
    """
    curp = payload.get("curp")
    rfc = payload.get("rfc")
    tipo_persona = payload.get("tipo_persona")
    llavemx_user_id = payload.get("llavemx_user_id")
    phone = payload.get("phone") or payload.get("phone_number")
    personas_morales = _parse_personas_morales(payload.get("personas_morales"))

    changed = False
    if curp and customer.curp != curp:
        customer.curp = curp
        changed = True
    if rfc and customer.rfc != rfc:
        customer.rfc = rfc
        changed = True
    if tipo_persona and customer.tipo_persona != tipo_persona:
        customer.tipo_persona = tipo_persona
        changed = True
    if llavemx_user_id is not None and customer.llavemx_user_id != str(llavemx_user_id):
        customer.llavemx_user_id = str(llavemx_user_id)
        changed = True
    if phone and customer.phone != phone:
        customer.phone = phone
        changed = True

    # Store the full structured Llave MX profile in metadata.
    if not isinstance(customer.metadata, dict):
        customer.metadata = {}
    llavemx_meta = {
        "curp": curp,
        "rfc": rfc,
        "tipo_persona": tipo_persona,
        "llavemx_user_id": str(llavemx_user_id) if llavemx_user_id is not None else None,
        "razon_social": payload.get("razon_social"),
        "personas_morales": personas_morales,
        "synced_at": datetime.utcnow().isoformat(),
    }
    if customer.metadata.get("llavemx") != llavemx_meta:
        customer.metadata["llavemx"] = llavemx_meta
        changed = True

    if changed:
        customer.updated_at = datetime.utcnow()


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

    # Resolve the customer. Prefer keycloak_id / curp to find the right record
    # even when email is absent or changed, then fall back to email.
    email = payload.get("email", "")
    curp = payload.get("curp")

    customer = await Customer.find_one(Customer.keycloak_id == customer_id)
    if customer is None and curp:
        customer = await Customer.find_one(Customer.curp == curp)
    if customer is None and email:
        customer = await Customer.find_one(Customer.email == email)

    if customer is None:
        # Create customer from Keycloak token if it doesn't exist.
        # Customer.email is required/unique; Llave MX accounts carry a correo,
        # but fall back to a CURP-derived address if absent.
        if not email:
            if not curp:
                raise credentials_exception
            email = f"{curp}@llavemx.local"
        username = payload.get("preferred_username", email)
        name = payload.get("name", username)

        customer = Customer(
            # Don't set id - let MongoDB generate it
            email=email,
            full_name=name,
            phone=payload.get("phone") or payload.get("phone_number") or "",
            document_number=curp or "",
            password_hash="",  # No password needed with Keycloak
            status=CustomerStatus.ACTIVE,
            email_verified=payload.get("email_verified", False),
            keycloak_id=customer_id  # Store Keycloak ID separately
        )

    # Keep the Keycloak link fresh and sync Llave MX data on every login.
    if customer.keycloak_id != customer_id:
        customer.keycloak_id = customer_id
    _sync_llavemx_profile(customer, payload)
    customer.update_last_login()
    await customer.save()

    # Run plugin-registered post-auth callbacks (e.g. tenant-specific entity sync).
    # Runs after save so customer.id is assigned; callbacks persist their own changes.
    await run_post_auth_callbacks(customer, payload, settings.TENANT_ID)

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

    # Build the citizen-facing step list. Branched workflows (e.g. RNPA
    # persona física / moral) use ShortCircuitOperator gates whose non-taken
    # branch is marked "skipped" by propagate_skips(). We hide both the
    # internal gates and the skipped branch so the citizen only sees their own
    # route, and we derive the route label from the gate that actually ran.
    from ...workflows.operators.python import ShortCircuitOperator

    step_progress = []
    route_label = None

    # Identify the non-taken branch so the citizen only sees their own route.
    # A ShortCircuitOperator gate that did NOT complete means its branch was
    # not taken. Reconstruction from the DB leaves that branch's steps as
    # "pending" (they were never executed and have no StepExecution record), so
    # there is no "skipped" status to rely on — we cascade from the un-taken
    # gates instead. Convergence steps survive because they also have a
    # completed (taken-branch) upstream, so not ALL their upstreams are dead.
    dead_branch = set()
    if dag_instance:
        def _status_of(tid):
            return dag_instance.task_states.get(tid, {}).get("status", "pending")

        for tid, task in dag_instance.dag.tasks.items():
            if isinstance(task, ShortCircuitOperator) and _status_of(tid) != "completed":
                dead_branch.add(tid)

        changed = True
        while changed:
            changed = False
            for tid in dag_instance.dag.tasks.keys():
                if tid in dead_branch or _status_of(tid) == "completed":
                    continue
                ups = list(dag_instance.dag.graph.predecessors(tid))
                if ups and all(u in dead_branch for u in ups):
                    dead_branch.add(tid)
                    changed = True

    if dag_instance:
        for task_id, state in dag_instance.task_states.items():
            status_val = state.get("status", "pending")
            task_obj = dag_instance.dag.tasks.get(task_id)

            # Branch gates are internal control steps, not citizen steps. The
            # gate that completed (rather than skipped) reveals the route taken.
            if isinstance(task_obj, ShortCircuitOperator):
                if status_val == "completed" and route_label is None:
                    gate_name = getattr(task_obj, "name", None) or ""
                    route_label = gate_name.replace("Rama ", "").strip() or None
                continue

            # Hide the non-taken branch (skipped, or pending-but-unreachable).
            if status_val == "skipped" or task_id in dead_branch:
                continue

            task_name = getattr(task_obj, 'name', None)
            if not task_name:
                i18n_key = f"steps.{task_id}"
                translated = translate(i18n_key, locale="es")
                task_name = translated if translated != i18n_key else task_id.replace("_", " ").title()
            task_group = getattr(task_obj, 'group', None)

            step_info = {
                "step_id": task_id,
                "name": task_name,
                "description": f"Step {task_id}",
                "status": status_val,
                "started_at": state.get("started_at"),
                "completed_at": state.get("completed_at")
            }
            if task_group:
                step_info["group"] = task_group
            step_progress.append(step_info)

    # Progress is computed over visible steps only (skipped branches and
    # internal gates excluded) so branched workflows report accurate progress.
    total_steps = len(step_progress)
    completed_steps = sum(1 for s in step_progress if s["status"] == "completed")
    progress_percentage = (completed_steps / total_steps * 100) if total_steps > 0 else 0

    # Resolve entities emitted by the workflow so the portal can show them
    # inline once the trámite concludes. Keep this light: ids/types only, never
    # entity.data (which may carry heavy base64 payloads). The portal fetches
    # full detail on demand via GET /public/entities/{id}.
    emitted_entities = []
    if dag_instance:
        ctx = dag_instance.context or {}
        seen_entity_ids = set()

        def _add_entity(entity_id, entity_type):
            if isinstance(entity_id, str) and entity_id and entity_id not in seen_entity_ids:
                seen_entity_ids.add(entity_id)
                emitted_entities.append({"entity_id": entity_id, "entity_type": entity_type})

        # Prefer created_entity_<type> keys (they carry the entity type).
        for key, value in ctx.items():
            if key.startswith("created_entity_"):
                _add_entity(value, key[len("created_entity_"):])
        # Then any *_entity_id outputs not already captured.
        for key, value in ctx.items():
            if key.endswith("_entity_id"):
                type_key = key[:-len("_entity_id")] + "_entity_type"
                _add_entity(value, ctx.get(type_key))

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
        "route_label": route_label,
        "emitted_entities": emitted_entities,
        "estimated_completion": None,
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