"""
Authentication module.

Provides:
- get_auth_service: AuthService singleton (app-specific config)
- get_current_user: FastAPI dependency for authenticated user  
- get_current_user_optional: Returns None if not authenticated
- CurrentUser: User model for authorization (from authz)

Note: Auth routes (login, register, etc.) are provided by app_kernel.
"""

from .deps import (
    get_auth_service,
    reset_auth_service,
    get_current_user,
    get_current_user_optional,
)

# Re-export CurrentUser from authz for convenience
from ..authz import CurrentUser

__all__ = [
    "get_auth_service",
    "reset_auth_service",
    "get_current_user",
    "get_current_user_optional",
    "CurrentUser",
]
