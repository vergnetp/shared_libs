"""
App-specific authentication.

Provides:
- get_auth_service: AuthService singleton (app-specific database config)
- get_current_user: Wraps kernel's auth to return app's CurrentUser

Note: Most auth functionality is in app_kernel. This module only provides
app-specific configuration and the CurrentUser adapter.
"""
from typing import Optional

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from backend.auth import (
    AuthService,
    AuthError,
    User,
    DatabaseUserStore,
    DatabaseRoleStore,
    MemoryUserStore,
    MemoryRoleStore,
)

from ...config import get_settings
from ..authz import CurrentUser


# =============================================================================
# Security Scheme
# =============================================================================

security = HTTPBearer(auto_error=False)


# =============================================================================
# Auth Service Singleton (App-Specific Config)
# =============================================================================

_auth_service: Optional[AuthService] = None


def get_auth_service() -> AuthService:
    """
    Get or create the auth service singleton.
    
    This is app-specific because it uses the app's database configuration.
    The kernel's auth router uses this via AuthServiceAdapter.
    """
    global _auth_service
    
    if _auth_service is None:
        settings = get_settings()
        
        if settings.auth_store == "database":
            user_store = DatabaseUserStore(
                db_type=settings.db_type,
                database=settings.db_path,
            )
            role_store = DatabaseRoleStore(
                db_type=settings.db_type,
                database=settings.db_path,
            )
        else:
            user_store = MemoryUserStore()
            role_store = MemoryRoleStore()
        
        _auth_service = AuthService(
            user_store=user_store,
            role_store=role_store,
            token_secret=settings.jwt_secret,
        )
    
    return _auth_service


def reset_auth_service():
    """Reset auth service (for testing)."""
    global _auth_service
    _auth_service = None


# =============================================================================
# Current User Dependency
# =============================================================================

async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    auth: AuthService = Depends(get_auth_service),
) -> CurrentUser:
    """
    Get the current authenticated user.
    
    Returns app's CurrentUser (from authz) for use with secure stores.
    If AUTH_ENABLED=false, returns a default admin user for development.
    """
    settings = get_settings()
    
    if not settings.auth_enabled:
        return CurrentUser(id="default", role="admin")
    
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    try:
        user = await auth.verify_token(credentials.credentials)
        request.state.user_id = user.id
        return CurrentUser.from_auth_user(user)
    except AuthError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user_optional(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    auth: AuthService = Depends(get_auth_service),
) -> Optional[CurrentUser]:
    """Get current user if authenticated, None otherwise."""
    settings = get_settings()
    
    if not settings.auth_enabled:
        return CurrentUser(id="default", role="admin")
    
    if not credentials:
        return None
    
    try:
        user = await auth.verify_token(credentials.credentials)
        request.state.user_id = user.id
        return CurrentUser.from_auth_user(user)
    except AuthError:
        return None
