"""
Workspace access checker for deploy_api.

Implements app_kernel's WorkspaceAccess protocol.
"""

from typing import Optional

from backend.app_kernel.db import get_db_connection
from .stores import WorkspaceStore


class DeployWorkspaceChecker:
    """
    Workspace access checker using DB-backed WorkspaceStore.
    
    Creates fresh DB sessions for each check to avoid stale connections.
    
    Register with kernel on startup:
        from app_kernel.access import workspace_access
        workspace_access.set_checker(DeployWorkspaceChecker())
    """
    
    async def is_member(self, user_id: str, workspace_id: str) -> bool:
        """Check if user is a member of the workspace."""
        async with get_db_connection() as conn:
            store = WorkspaceStore(conn)
            return await store.is_member(user_id, workspace_id)
    
    async def is_owner(self, user_id: str, workspace_id: str) -> bool:
        """Check if user is the owner of the workspace."""
        async with get_db_connection() as conn:
            store = WorkspaceStore(conn)
            return await store.is_owner(user_id, workspace_id)
    
    async def get_role(self, user_id: str, workspace_id: str) -> Optional[str]:
        """Get user's role in the workspace."""
        async with get_db_connection() as conn:
            store = WorkspaceStore(conn)
            return await store.get_role(user_id, workspace_id)
