"""
SaaS module FastAPI dependencies.

Provides:
- require_workspace_member: User must be member of workspace
- require_workspace_admin: User must be admin/owner of workspace
- require_workspace_owner: User must be owner of workspace
"""

from typing import Optional
from fastapi import Depends, HTTPException, status, Request

from ..auth.deps import get_current_user
from ..auth.models import UserIdentity
from ..db import get_db_connection
from .stores import MemberStore, WorkspaceStore


async def get_workspace_store():
    """Get workspace store."""
    async with get_db_connection() as conn:
        yield WorkspaceStore(conn)


async def get_member_store():
    """Get member store."""
    async with get_db_connection() as conn:
        yield MemberStore(conn)


class WorkspaceMemberChecker:
    """
    Dependency that checks workspace membership.
    
    Usage:
        @router.get("/workspaces/{workspace_id}/...")
        async def endpoint(
            workspace_id: str,
            current_user: UserIdentity = Depends(require_workspace_member),
        ):
            ...
    """
    
    def __init__(self, require_admin: bool = False, require_owner: bool = False):
        self.require_admin = require_admin
        self.require_owner = require_owner
    
    async def __call__(
        self,
        request: Request,
        current_user: UserIdentity = Depends(get_current_user),
    ) -> UserIdentity:
        # Extract workspace_id from path
        workspace_id = request.path_params.get("workspace_id")
        if not workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="workspace_id not found in path",
            )
        
        async with get_db_connection() as conn:
            member_store = MemberStore(conn)
            
            # Check membership
            member = await member_store.get(workspace_id, current_user.id)
            if not member:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Not a member of this workspace",
                )
            
            # Check owner requirement
            if self.require_owner:
                if member.get("role") != "owner":
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Owner access required",
                    )
            
            # Check admin requirement
            elif self.require_admin:
                if member.get("role") not in ("owner", "admin"):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Admin access required",
                    )
            
            # Attach workspace info to user for convenience
            current_user.workspace_id = workspace_id
            current_user.workspace_role = member.get("role")
            
            return current_user


# Pre-configured dependency instances
require_workspace_member = WorkspaceMemberChecker()
require_workspace_admin = WorkspaceMemberChecker(require_admin=True)
require_workspace_owner = WorkspaceMemberChecker(require_owner=True)


async def get_or_create_personal_workspace(user_id: str, user_email: str) -> dict:
    """
    Get or create user's personal workspace.
    Called during signup when include_saas=True.
    """
    async with get_db_connection() as conn:
        store = WorkspaceStore(conn)
        
        # Check for existing personal workspace
        workspace = await store.get_personal_workspace(user_id)
        if workspace:
            return workspace
        
        # Create personal workspace
        name = user_email.split("@")[0]  # Use email prefix as name
        workspace = await store.create(
            name=f"{name}'s Workspace",
            owner_id=user_id,
            is_personal=True,
        )
        
        return workspace
