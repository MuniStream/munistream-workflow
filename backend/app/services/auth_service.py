"""
Authentication service for JWT token management and user authentication.
"""

import jwt
from jwt.exceptions import InvalidTokenError, ExpiredSignatureError
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from fastapi import HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import Depends, Request
from passlib.context import CryptContext

from ..models.user import UserModel, RefreshTokenModel, UserRole, Permission
from ..core.config import settings

# JWT Configuration
SECRET_KEY = settings.SECRET_KEY
ALGORITHM = settings.ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES
REFRESH_TOKEN_EXPIRE_DAYS = 7

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

security = HTTPBearer()

class AuthService:
    """Authentication service for handling JWT tokens and user authentication"""
    
    @staticmethod
    def get_password_hash(password: str) -> str:
        """Hash a password using bcrypt"""
        return pwd_context.hash(password)
    
    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash"""
        return pwd_context.verify(plain_password, hashed_password)
    
    @staticmethod
    def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
        """Create JWT access token"""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        
        to_encode.update({"exp": expire, "type": "access"})
        encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
        return encoded_jwt
    
    @staticmethod
    def create_refresh_token(user_id: str, device_info: Optional[str] = None, ip_address: Optional[str] = None) -> str:
        """Create refresh token and store in database"""
        token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        
        refresh_token = RefreshTokenModel(
            user_id=user_id,
            token=token,
            expires_at=expires_at,
            device_info=device_info,
            ip_address=ip_address
        )
        return token
    
    @staticmethod
    def verify_token(token: str) -> Dict[str, Any]:
        """Verify JWT token and return payload"""
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            return payload
        except ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except InvalidTokenError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )
    
    @staticmethod
    async def authenticate_user(username: str, password: str) -> Optional[UserModel]:
        """Authenticate user with username/email and password"""
        # Try to find user by username or email
        user = await UserModel.find_one(
            {"$or": [{"username": username}, {"email": username}]}
        )
        
        if not user:
            return None
        
        # Check if account is locked
        if user.is_locked():
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail=f"Account is locked until {user.locked_until}"
            )
        
        # Check if account is active
        if user.status != "active":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is not active"
            )
        
        # Verify password
        if not user.verify_password(password):
            user.increment_failed_attempts()
            await user.save()
            return None
        
        # Reset failed attempts and update last login
        user.update_last_login()
        await user.save()
        
        return user
    
    @staticmethod
    async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> UserModel:
        """Get current authenticated user from JWT token"""
        token = credentials.credentials
        payload = AuthService.verify_token(token)
        
        # Check token type
        if payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Get user from database
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        user = await UserModel.get(user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Check if user is still active
        if user.status != "active":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is not active"
            )
        
        return user
    
    @staticmethod
    async def refresh_access_token(refresh_token: str) -> Dict[str, Any]:
        """Refresh access token using refresh token"""
        # Find refresh token in database
        token_doc = await RefreshTokenModel.find_one({"token": refresh_token})
        
        if not token_doc or not token_doc.is_valid():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token"
            )
        
        # Get user
        user = await UserModel.get(token_doc.user_id)
        if not user or user.status != "active":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or inactive"
            )
        
        # Create new access token
        access_token = AuthService.create_access_token(
            data={"sub": str(user.id), "username": user.username, "role": user.role}
        )
        
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60
        }
    
    @staticmethod
    async def revoke_refresh_token(refresh_token: str):
        """Revoke a refresh token"""
        token_doc = await RefreshTokenModel.find_one({"token": refresh_token})
        if token_doc:
            token_doc.revoke()
            await token_doc.save()
    
    @staticmethod
    async def revoke_all_user_tokens(user_id: str):
        """Revoke all refresh tokens for a user"""
        tokens = await RefreshTokenModel.find({"user_id": user_id, "is_revoked": False}).to_list()
        for token in tokens:
            token.revoke()
            await token.save()

# Permission decorators and dependencies
def require_permission(permission: Permission):
    """Decorator to require specific permission"""
    async def permission_dependency(current_user: UserModel = Depends(AuthService.get_current_user)):
        if not current_user.has_permission(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission required: {permission.value}"
            )
        return current_user
    return permission_dependency

def require_role(role: UserRole):
    """Decorator to require specific role"""
    async def role_dependency(current_user: UserModel = Depends(AuthService.get_current_user)):
        if current_user.role != role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role required: {role.value}"
            )
        return current_user
    return role_dependency

def require_any_role(roles: list[UserRole]):
    """Decorator to require any of the specified roles"""
    async def role_dependency(current_user: UserModel = Depends(AuthService.get_current_user)):
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"One of these roles required: {[r.value for r in roles]}"
            )
        return current_user
    return role_dependency

def require_admin():
    """Shortcut to require admin role"""
    return require_role(UserRole.ADMIN)

def require_manager_or_admin():
    """Shortcut to require manager or admin role"""
    return require_any_role([UserRole.MANAGER, UserRole.ADMIN])

# Get current user dependency (no permission required)
async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> UserModel:
    """Get current user dependency"""
    return await AuthService.get_current_user(credentials)

# Optional authentication (for endpoints that work with or without auth)
async def get_current_user_optional(request: Request) -> Optional[UserModel]:
    """Get current user if authenticated, None otherwise"""
    try:
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return None
        
        token = auth_header.split(" ")[1]
        payload = AuthService.verify_token(token)
        
        if payload.get("type") != "access":
            return None
        
        user_id = payload.get("sub")
        if not user_id:
            return None
        
        user = await UserModel.get(user_id)
        if not user or user.status != "active":
            return None
        
        return user
    except:
        return None