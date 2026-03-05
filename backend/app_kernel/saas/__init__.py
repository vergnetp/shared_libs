"""
SaaS module for app_kernel.

Provides multi-tenant workspace/team functionality:
- Workspaces (teams/organizations)
- Members with roles (owner/admin/member)
- Invites with token-based acceptance
- Projects (deployment groupings within workspaces)

Usage:
    # In create_app
    app = create_app(
        title="My SaaS",
        include_auth=True,
        include_saas=True,  # Enables SaaS features
    )
    
    # In your routes
    from ..saas import require_workspace_member
    
    @router.get("/workspaces/{workspace_id}/data")
    async def get_data(
        workspace_id: str,
        current_user = Depends(require_workspace_member),
    ):
        ...
"""

from .stores import WorkspaceStore, MemberStore, InviteStore, ProjectStore
from .deps import (
    require_workspace_member,
    require_workspace_admin,
    require_workspace_owner,
    get_or_create_personal_workspace,
)
from .router import create_saas_router
from .email import set_email_sender, get_email_sender, send_invite_email

__all__ = [
    # Stores
    "WorkspaceStore",
    "MemberStore", 
    "InviteStore",
    "ProjectStore",
    # Dependencies
    "require_workspace_member",
    "require_workspace_admin",
    "require_workspace_owner",
    "get_or_create_personal_workspace",
    # Router
    "create_saas_router",
    # Email
    "set_email_sender",
    "get_email_sender",
    "send_invite_email",
]
