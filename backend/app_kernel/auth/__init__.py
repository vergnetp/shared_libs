"""
app_kernel.auth - Authentication primitives.

This module provides:
- User identity models
- FastAPI dependencies for auth
- Token creation/verification utilities
- Password hashing
- Generic auth router (login/register/me/change-password/logout)
- AuthServiceAdapter for backend.auth integration

Usage:
    from app_kernel.auth import get_current_user, require_admin, UserIdentity
    
    @app.get("/profile")
    async def profile(user: UserIdentity = Depends(get_current_user)):
        return {"id": user.id}
"""

from .models import UserIdentity, TokenPayload, RequestContext
from .deps import (
    get_current_user,
    get_current_user_optional,
    require_admin,
    get_request_context,
    init_auth_deps,
    AuthDependencies,
)
from .utils import (
    AuthError,
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_token,
)
from .router import create_auth_router, UserStore, AuthServiceAdapter

__all__ = [
    # Models
    "UserIdentity",
    "TokenPayload", 
    "RequestContext",
    
    # Dependencies
    "get_current_user",
    "get_current_user_optional",
    "require_admin",
    "get_request_context",
    "init_auth_deps",
    "AuthDependencies",
    
    # Utilities
    "AuthError",
    "hash_password",
    "verify_password",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "verify_token",
    
    # Router
    "create_auth_router",
    "UserStore",
    "AuthServiceAdapter",
]
