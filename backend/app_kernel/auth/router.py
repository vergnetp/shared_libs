"""
Generic auth router.

Provides:
- POST /login: Authenticate and get tokens
- GET /me: Get current user info
- POST /register: Register new user
- POST /refresh: Refresh access token
- POST /change-password: Change password
- POST /logout: Logout (stateless - client discards tokens)

This router is generic - no domain concepts.
Apps can extend or replace it as needed.
"""

from typing import Optional, Callable, Awaitable
from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, EmailStr, Field

from .models import UserIdentity
from .utils import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    AuthError,
)


# =============================================================================
# Request/Response Models
# =============================================================================

class LoginRequest(BaseModel):
    """Login request."""
    username: str = Field(..., description="Username or email")
    password: str = Field(..., min_length=1)


class RegisterRequest(BaseModel):
    """Registration request."""
    username: str = Field(..., min_length=3, max_length=50)
    email: Optional[EmailStr] = None
    password: str = Field(..., min_length=8)


class TokenResponse(BaseModel):
    """Token response."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Access token expiry in seconds")


class RefreshRequest(BaseModel):
    """Token refresh request."""
    refresh_token: str


class UserResponse(BaseModel):
    """User info response."""
    id: str
    username: str
    email: Optional[str] = None
    role: str = "user"


class ChangePasswordRequest(BaseModel):
    """Change password request."""
    old_password: str
    new_password: str = Field(..., min_length=8)


class MessageResponse(BaseModel):
    """Simple message response."""
    message: str


# =============================================================================
# User Store Protocol
# =============================================================================

class UserStore:
    """
    Protocol for user storage.
    
    Apps must provide an implementation when creating the auth router.
    This keeps the kernel generic - no database assumptions.
    """
    
    async def get_by_username(self, username: str) -> Optional[dict]:
        """
        Get user by username or email.
        
        Returns dict with: id, username, email, password_hash, role
        Returns None if not found.
        """
        raise NotImplementedError
    
    async def get_by_id(self, user_id: str) -> Optional[dict]:
        """Get user by ID."""
        raise NotImplementedError
    
    async def create(self, username: str, email: str, password_hash: str) -> dict:
        """
        Create new user.
        
        Returns created user dict (without password_hash).
        Raises ValueError if username/email already exists.
        """
        raise NotImplementedError
    
    async def update_password(self, user_id: str, password_hash: str) -> bool:
        """
        Update user's password hash.
        
        Returns True if updated, False if user not found.
        """
        raise NotImplementedError


# =============================================================================
# AuthService Adapter
# =============================================================================

class AuthServiceAdapter(UserStore):
    """
    Adapter that wraps backend.auth.AuthService to implement UserStore protocol.
    
    This allows using the kernel's generic auth router with the existing
    backend.auth user storage.
    
    Usage:
        from ...auth import AuthService
        from ..auth import AuthServiceAdapter
        
        auth_service = AuthService(...)
        user_store = AuthServiceAdapter(auth_service)
        
        init_app_kernel(app, settings, user_store=user_store)
    """
    
    def __init__(self, auth_service):
        """
        Args:
            auth_service: Instance of backend.auth.AuthService
        """
        self.auth = auth_service
    
    async def get_by_username(self, username: str) -> Optional[dict]:
        """Get user by username (email)."""
        try:
            user = await self.auth.user_store.get_by_email(username)
            if user is None:
                return None
            return self._user_to_dict(user)
        except Exception:
            return None
    
    async def get_by_id(self, user_id: str) -> Optional[dict]:
        """Get user by ID."""
        try:
            user = await self.auth.user_store.get(user_id)
            if user is None:
                return None
            return self._user_to_dict(user)
        except Exception:
            return None
    
    async def create(self, username: str, email: str, password_hash: str) -> dict:
        """Create new user with pre-hashed password."""
        # Check if user exists
        existing = await self.auth.user_store.get_by_email(email or username)
        if existing:
            raise ValueError(f"User with email {email or username} already exists")
        
        # Import User model from ...auth
        from ...auth import User
        
        user = User(
            email=email or username,
            name=username,
            password_hash=password_hash,
            role="user",
            is_active=True,
        )
        
        created = await self.auth.user_store.create(user)
        return self._user_to_dict(created)
    
    async def update_password(self, user_id: str, password_hash: str) -> bool:
        """Update user's password."""
        try:
            user = await self.auth.user_store.get(user_id)
            if user is None:
                return False
            
            user.password_hash = password_hash
            await self.auth.user_store.update(user)
            return True
        except Exception:
            return False
    
    def _user_to_dict(self, user) -> dict:
        """Convert User object to dict."""
        return {
            "id": user.id,
            "username": user.email,
            "email": user.email,
            "password_hash": user.password_hash,
            "role": getattr(user, "role", None) or getattr(user, "metadata", {}).get("role", "user"),
            "name": getattr(user, "name", None),
            "is_active": getattr(user, "is_active", True),
            "created_at": getattr(user, "created_at", None),
        }


# =============================================================================
# Router Factory
# =============================================================================

def create_auth_router(
    user_store: UserStore,
    token_secret: str,
    access_token_expires_minutes: int = 15,
    refresh_token_expires_days: int = 30,
    allow_self_signup: bool = True,  # Kept for backwards compat, always True
    prefix: str = "/auth",
    on_signup: Callable = None,  # async callback(user_id, user_email) called after signup
) -> APIRouter:
    """
    Create auth router.
    
    Args:
        user_store: Implementation of UserStore protocol
        token_secret: Secret key for JWT tokens
        access_token_expires_minutes: Access token TTL
        refresh_token_expires_days: Refresh token TTL
        allow_self_signup: Deprecated, registration always enabled
        prefix: URL prefix for routes
        on_signup: Optional async callback called after successful signup.
                   Signature: async (user_id: str, user_email: str) -> None
        
    Returns:
        FastAPI router with auth endpoints
    """
    router = APIRouter(prefix=prefix, tags=["Authentication"])
    
    @router.post(
        "/login",
        response_model=TokenResponse,
        summary="Login",
        description="Authenticate with username/password and receive tokens.",
    )
    async def login(request: LoginRequest):
        """
        Authenticate user and return JWT tokens.
        
        Returns access_token (short-lived) and refresh_token (long-lived).
        """
        # Get user
        user = await user_store.get_by_username(request.username)
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )
        
        # Verify password
        if not verify_password(request.password, user.get("password_hash", "")):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )
        
        # Create tokens
        access_token = create_access_token(
            user_id=user["id"],
            role=user.get("role", "user"),
            email=user.get("email", ""),
            secret=token_secret,
            expires_minutes=access_token_expires_minutes,
        )
        
        refresh_token = create_refresh_token(
            user_id=user["id"],
            secret=token_secret,
            expires_days=refresh_token_expires_days,
        )
        
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=access_token_expires_minutes * 60,
        )
    
    @router.post(
        "/refresh",
        response_model=TokenResponse,
        summary="Refresh token",
        description="Exchange refresh token for new access token.",
    )
    async def refresh(request: RefreshRequest):
        """
        Refresh access token using refresh token.
        """
        try:
            payload = decode_token(request.refresh_token, token_secret)
        except AuthError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(e),
            )
        
        # Verify user still exists
        user = await user_store.get_by_id(payload.user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )
        
        # Create new access token
        access_token = create_access_token(
            user_id=user["id"],
            role=user.get("role", "user"),
            email=user.get("email", ""),
            secret=token_secret,
            expires_minutes=access_token_expires_minutes,
        )
        
        # Optionally rotate refresh token (more secure)
        new_refresh_token = create_refresh_token(
            user_id=user["id"],
            secret=token_secret,
            expires_days=refresh_token_expires_days,
        )
        
        return TokenResponse(
            access_token=access_token,
            refresh_token=new_refresh_token,
            expires_in=access_token_expires_minutes * 60,
        )
    
    @router.get(
        "/me",
        response_model=UserResponse,
        summary="Current user",
        description="Get current authenticated user info.",
    )
    async def me(request: Request):
        """
        Get current user info.
        
        Requires valid access token in Authorization header.
        """
        # Extract token from header
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing authorization header",
            )
        
        token = auth_header[7:]  # Remove "Bearer "
        
        try:
            payload = decode_token(token, token_secret)
        except AuthError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(e),
            )
        
        user = await user_store.get_by_id(payload.user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )
        
        return UserResponse(
            id=user["id"],
            username=user.get("username", ""),
            email=user.get("email"),
            role=user.get("role", "user"),
        )
    
    @router.post(
        "/change-password",
        response_model=MessageResponse,
        summary="Change password",
        description="Change current user's password.",
    )
    async def change_password(request_body: ChangePasswordRequest, request: Request):
        """
        Change password for current user.
        
        Requires current password verification.
        """
        # Extract token from header
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing authorization header",
            )
        
        token = auth_header[7:]
        
        try:
            payload = decode_token(token, token_secret)
        except AuthError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(e),
            )
        
        # Get user
        user = await user_store.get_by_id(payload.user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )
        
        # Verify old password
        if not verify_password(request_body.old_password, user.get("password_hash", "")):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid current password",
            )
        
        # Update password
        new_hash = hash_password(request_body.new_password)
        success = await user_store.update_password(payload.user_id, new_hash)
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update password",
            )
        
        return MessageResponse(message="Password changed successfully")
    
    @router.post(
        "/logout",
        response_model=MessageResponse,
        summary="Logout",
        description="Logout current user.",
    )
    async def logout(request: Request):
        """
        Logout current user.
        
        With stateless JWT, this is a no-op on server.
        Client should discard tokens.
        
        For proper logout, implement token blacklist.
        """
        # Verify token is valid (optional, but good practice)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            try:
                decode_token(token, token_secret)
            except AuthError:
                pass  # Token invalid, but we don't care for logout
        
        # TODO: If using token blacklist, add current token here
        return MessageResponse(message="Logged out successfully")
    
    @router.post(
        "/register",
        response_model=UserResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Register",
        description="Create new user account.",
    )
    async def register(request: RegisterRequest):
        """Register new user."""
        # Hash password
        password_hash = hash_password(request.password)
        
        try:
            user = await user_store.create(
                username=request.username,
                email=request.email or "",
                password_hash=password_hash,
            )
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            )
        
        # Call on_signup callback if provided (e.g., to create personal workspace)
        if on_signup:
            try:
                await on_signup(user["id"], user.get("email", ""))
            except Exception:
                pass  # Don't fail signup if callback fails
        
        return UserResponse(
            id=user["id"],
            username=user.get("username", ""),
            email=user.get("email"),
            role=user.get("role", "user"),
        )
    
    return router
