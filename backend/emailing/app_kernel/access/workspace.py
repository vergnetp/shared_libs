"""
Workspace access primitives.

The kernel provides the MECHANISM for workspace-scoped access checks.
The kernel does NOT define what a "workspace" means - that is app domain logic.

Apps:
- Define their own workspace/membership tables
- Implement the WorkspaceAccess protocol
- Register their checker with the kernel

Kernel:
- Provides the protocol interface
- Calls into app-provided checker
- Enforces the app's access decisions (deny if checker returns False)

Usage:
    from app_kernel.access import WorkspaceAccess, require_workspace_member
    
    # In your app, implement the protocol
    class MyWorkspaceChecker(WorkspaceAccess):
        async def is_member(self, user_id: str, workspace_id: str) -> bool:
            return await db.check_membership(user_id, workspace_id)
    
    # Register with kernel
    workspace_access.set_checker(MyWorkspaceChecker())
    
    # Use in routes
    @app.get("/workspace/{workspace_id}/data")
    async def get_data(
        workspace_id: str,
        user: UserIdentity = Depends(get_current_user),
        _: None = Depends(require_workspace_member)
    ):
        ...
"""
from typing import Protocol, Optional, runtime_checkable
from fastapi import Request, HTTPException, Depends

from ..auth.models import UserIdentity
from ..auth.deps import get_current_user


@runtime_checkable
class WorkspaceAccess(Protocol):
    """
    Protocol for workspace membership checks.
    
    Apps implement this to provide their own membership logic.
    """
    
    async def is_member(self, user_id: str, workspace_id: str) -> bool:
        """Check if user is a member of the workspace."""
        ...
    
    async def is_owner(self, user_id: str, workspace_id: str) -> bool:
        """Check if user is the owner of the workspace."""
        ...
    
    async def get_role(self, user_id: str, workspace_id: str) -> Optional[str]:
        """Get user's role in the workspace (e.g., 'owner', 'admin', 'member')."""
        ...


class DefaultWorkspaceAccess:
    """
    Default workspace access that allows all access.
    
    Apps should replace this with their own implementation.
    """
    
    async def is_member(self, user_id: str, workspace_id: str) -> bool:
        return True
    
    async def is_owner(self, user_id: str, workspace_id: str) -> bool:
        return False
    
    async def get_role(self, user_id: str, workspace_id: str) -> Optional[str]:
        return "member"


class WorkspaceAccessRegistry:
    """
    Registry for workspace access checker.
    
    Holds the app-provided implementation of WorkspaceAccess.
    """
    
    def __init__(self):
        self._checker: WorkspaceAccess = DefaultWorkspaceAccess()
    
    def set_checker(self, checker: WorkspaceAccess):
        """Set the workspace access checker implementation."""
        self._checker = checker
    
    @property
    def checker(self) -> WorkspaceAccess:
        return self._checker


# Global registry instance
workspace_access = WorkspaceAccessRegistry()


def _extract_workspace_id(request: Request) -> Optional[str]:
    """Extract workspace_id from request path parameters."""
    return request.path_params.get("workspace_id")


async def require_workspace_member(
    request: Request,
    user: UserIdentity = Depends(get_current_user)
) -> UserIdentity:
    """
    Dependency that requires the user to be a workspace member.
    
    Extracts workspace_id from path parameters.
    Raises 403 if user is not a member.
    """
    workspace_id = _extract_workspace_id(request)
    
    if not workspace_id:
        raise HTTPException(
            status_code=400, 
            detail="workspace_id not found in path"
        )
    
    is_member = await workspace_access.checker.is_member(user.id, workspace_id)
    
    if not is_member:
        raise HTTPException(
            status_code=403,
            detail="Not a member of this workspace"
        )
    
    return user


async def require_workspace_owner(
    request: Request,
    user: UserIdentity = Depends(get_current_user)
) -> UserIdentity:
    """
    Dependency that requires the user to be the workspace owner.
    
    Raises 403 if user is not the owner.
    """
    workspace_id = _extract_workspace_id(request)
    
    if not workspace_id:
        raise HTTPException(
            status_code=400,
            detail="workspace_id not found in path"
        )
    
    is_owner = await workspace_access.checker.is_owner(user.id, workspace_id)
    
    if not is_owner:
        raise HTTPException(
            status_code=403,
            detail="Workspace owner access required"
        )
    
    return user


async def get_workspace_role(
    request: Request,
    user: UserIdentity = Depends(get_current_user)
) -> Optional[str]:
    """
    Get the user's role in the workspace.
    
    Returns None if not a member.
    """
    workspace_id = _extract_workspace_id(request)
    
    if not workspace_id:
        return None
    
    return await workspace_access.checker.get_role(user.id, workspace_id)
