"""
app_kernel.access - Access control primitives.

This module provides:
- Workspace membership checks
- Scope-based permission checks

Apps implement the protocols to provide their own logic.

Usage:
    from app_kernel.access import (
        workspace_access, 
        require_workspace_member,
        require_scope,
        check_scope
    )
"""

from .workspace import (
    WorkspaceAccess,
    DefaultWorkspaceAccess,
    WorkspaceAccessRegistry,
    workspace_access,
    require_workspace_member,
    require_workspace_owner,
    get_workspace_role,
)

from .scope import (
    ScopeChecker,
    DefaultScopeChecker,
    ScopeRegistry,
    scope_registry,
    require_scope,
    check_scope,
    get_user_scopes,
)

__all__ = [
    # Workspace
    "WorkspaceAccess",
    "DefaultWorkspaceAccess",
    "WorkspaceAccessRegistry",
    "workspace_access",
    "require_workspace_member",
    "require_workspace_owner",
    "get_workspace_role",
    
    # Scope
    "ScopeChecker",
    "DefaultScopeChecker",
    "ScopeRegistry",
    "scope_registry",
    "require_scope",
    "check_scope",
    "get_user_scopes",
]
