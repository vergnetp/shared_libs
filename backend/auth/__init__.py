"""
Auth module - Authentication and authorization for shared_lib.

Usage:
    from auth import AuthService, DatabaseUserStore, DatabaseRoleStore
    
    # Setup
    auth = AuthService(
        user_store=DatabaseUserStore("postgres", database="myapp"),
        role_store=DatabaseRoleStore("postgres", database="myapp"),
        token_secret=os.environ["JWT_SECRET"]
    )
    
    # Register & login
    user = await auth.register("alice@example.com", "password123")
    user, access_token, refresh_token = await auth.login("alice@example.com", "password123")
    
    # Verify token
    user = await auth.verify_token(access_token)
    
    # Roles & permissions
    await auth.create_role("editor", ["read", "write", "comment"])
    await auth.assign_role(user.id, "editor", resource_type="project", resource_id="proj-123")
    
    can_write = await auth.has_permission(user.id, "write", "project", "proj-123")
"""

from .models import User, Role, RoleAssignment, Session
from .stores import (
    UserStore,
    RoleStore,
    DatabaseUserStore,
    DatabaseRoleStore,
    MemoryUserStore,
    MemoryRoleStore,
)
from .service import AuthService, AuthError
from .hashing import hash_password, verify_password, generate_token, hash_token
from .tokens import create_jwt, decode_jwt, create_access_token, create_refresh_token, TokenError

__all__ = [
    # Models
    "User",
    "Role",
    "RoleAssignment",
    "Session",
    # Stores
    "UserStore",
    "RoleStore",
    "DatabaseUserStore",
    "DatabaseRoleStore",
    "MemoryUserStore",
    "MemoryRoleStore",
    # Service
    "AuthService",
    "AuthError",
    # Utilities
    "hash_password",
    "verify_password",
    "generate_token",
    "hash_token",
    "create_jwt",
    "decode_jwt",
    "create_access_token",
    "create_refresh_token",
    "TokenError",
]
