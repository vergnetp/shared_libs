"""
SaaS module API routes.

Endpoints for:
- Workspaces: CRUD, list
- Members: List, update role, remove
- Invites: Create, list, cancel, accept
- Projects: CRUD within workspaces
"""

from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, EmailStr

from ..auth.deps import get_current_user
from ..auth.models import UserIdentity
from ..db import get_db_connection
from .stores import WorkspaceStore, MemberStore, InviteStore, ProjectStore
from .deps import require_workspace_member, require_workspace_admin, require_workspace_owner


# =============================================================================
# Schemas
# =============================================================================

class WorkspaceCreate(BaseModel):
    name: str
    slug: Optional[str] = None


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None


class WorkspaceResponse(BaseModel):
    id: str
    name: str
    slug: str
    owner_id: str
    is_personal: bool
    role: Optional[str] = None  # User's role in this workspace
    created_at: str


class MemberResponse(BaseModel):
    id: str
    user_id: str
    role: str
    joined_at: str
    # Could add user details here if needed


class MemberRoleUpdate(BaseModel):
    role: str  # admin, member


class InviteCreate(BaseModel):
    email: EmailStr
    role: str = "member"


class InviteResponse(BaseModel):
    id: str
    email: str
    role: str
    status: str
    token: Optional[str] = None  # Only shown to creator
    invite_url: Optional[str] = None
    expires_at: str
    created_at: str


class InviteAcceptResponse(BaseModel):
    workspace_id: str
    workspace_name: str
    role: str


class ProjectCreate(BaseModel):
    name: str
    slug: Optional[str] = None
    description: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None


class ProjectResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    slug: str
    description: Optional[str] = None
    settings: Dict[str, Any] = {}
    created_by: str
    created_at: str
    updated_at: str


# =============================================================================
# Router Factory
# =============================================================================

def create_saas_router(
    prefix: str = "",
    invite_base_url: str = None,  # e.g., "https://app.example.com/invite"
) -> APIRouter:
    """
    Create SaaS router with workspace/member/invite endpoints.
    
    Args:
        prefix: URL prefix for routes
        invite_base_url: Base URL for invite links (token appended)
    """
    router = APIRouter(prefix=prefix, tags=["workspaces"])
    
    # =========================================================================
    # Workspaces
    # =========================================================================
    
    @router.post("/workspaces", response_model=WorkspaceResponse)
    async def create_workspace(
        data: WorkspaceCreate,
        current_user: UserIdentity = Depends(get_current_user),
    ):
        """Create a new workspace."""
        async with get_db_connection() as conn:
            store = WorkspaceStore(conn)
            workspace = await store.create(
                name=data.name,
                slug=data.slug,
                owner_id=current_user.id,
            )
            workspace["is_personal"] = bool(workspace.get("is_personal"))
            workspace["role"] = "owner"
            return workspace
    
    @router.get("/workspaces", response_model=List[WorkspaceResponse])
    async def list_workspaces(
        current_user: UserIdentity = Depends(get_current_user),
    ):
        """List all workspaces user is a member of."""
        async with get_db_connection() as conn:
            store = WorkspaceStore(conn)
            workspaces = await store.list_for_user(current_user.id)
            for ws in workspaces:
                ws["is_personal"] = bool(ws.get("is_personal"))
            return workspaces
    
    @router.get("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
    async def get_workspace(
        workspace_id: str,
        current_user: UserIdentity = Depends(require_workspace_member),
    ):
        """Get workspace details."""
        async with get_db_connection() as conn:
            store = WorkspaceStore(conn)
            workspace = await store.get(workspace_id)
            if not workspace:
                raise HTTPException(status_code=404, detail="Workspace not found")
            workspace["is_personal"] = bool(workspace.get("is_personal"))
            workspace["role"] = current_user.workspace_role
            return workspace
    
    @router.patch("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
    async def update_workspace(
        workspace_id: str,
        data: WorkspaceUpdate,
        current_user: UserIdentity = Depends(require_workspace_admin),
    ):
        """Update workspace (admin only)."""
        async with get_db_connection() as conn:
            store = WorkspaceStore(conn)
            updates = data.model_dump(exclude_unset=True)
            workspace = await store.update(workspace_id, updates)
            if not workspace:
                raise HTTPException(status_code=404, detail="Workspace not found")
            workspace["is_personal"] = bool(workspace.get("is_personal"))
            workspace["role"] = current_user.workspace_role
            return workspace
    
    @router.delete("/workspaces/{workspace_id}", status_code=204)
    async def delete_workspace(
        workspace_id: str,
        current_user: UserIdentity = Depends(require_workspace_owner),
    ):
        """Delete workspace (owner only). Cannot delete personal workspace."""
        async with get_db_connection() as conn:
            store = WorkspaceStore(conn)
            workspace = await store.get(workspace_id)
            if not workspace:
                raise HTTPException(status_code=404, detail="Workspace not found")
            if workspace.get("is_personal"):
                raise HTTPException(status_code=400, detail="Cannot delete personal workspace")
            await store.delete(workspace_id)
    
    # =========================================================================
    # Members
    # =========================================================================
    
    @router.get("/workspaces/{workspace_id}/members", response_model=List[MemberResponse])
    async def list_members(
        workspace_id: str,
        current_user: UserIdentity = Depends(require_workspace_member),
    ):
        """List workspace members."""
        async with get_db_connection() as conn:
            store = MemberStore(conn)
            return await store.list_for_workspace(workspace_id)
    
    @router.patch("/workspaces/{workspace_id}/members/{user_id}", response_model=MemberResponse)
    async def update_member_role(
        workspace_id: str,
        user_id: str,
        data: MemberRoleUpdate,
        current_user: UserIdentity = Depends(require_workspace_admin),
    ):
        """Update member's role (admin only). Cannot change owner's role."""
        if data.role not in ("admin", "member"):
            raise HTTPException(status_code=400, detail="Role must be 'admin' or 'member'")
        
        async with get_db_connection() as conn:
            store = MemberStore(conn)
            member = await store.get(workspace_id, user_id)
            if not member:
                raise HTTPException(status_code=404, detail="Member not found")
            if member.get("role") == "owner":
                raise HTTPException(status_code=400, detail="Cannot change owner's role")
            
            return await store.update_role(workspace_id, user_id, data.role)
    
    @router.delete("/workspaces/{workspace_id}/members/{user_id}", status_code=204)
    async def remove_member(
        workspace_id: str,
        user_id: str,
        current_user: UserIdentity = Depends(require_workspace_admin),
    ):
        """Remove member from workspace (admin only). Cannot remove owner."""
        async with get_db_connection() as conn:
            store = MemberStore(conn)
            member = await store.get(workspace_id, user_id)
            if not member:
                raise HTTPException(status_code=404, detail="Member not found")
            if member.get("role") == "owner":
                raise HTTPException(status_code=400, detail="Cannot remove owner")
            await store.remove(workspace_id, user_id)
    
    @router.delete("/workspaces/{workspace_id}/leave", status_code=204)
    async def leave_workspace(
        workspace_id: str,
        current_user: UserIdentity = Depends(require_workspace_member),
    ):
        """Leave a workspace. Owner cannot leave (must transfer ownership first)."""
        async with get_db_connection() as conn:
            store = MemberStore(conn)
            member = await store.get(workspace_id, current_user.id)
            if member.get("role") == "owner":
                raise HTTPException(status_code=400, detail="Owner cannot leave. Transfer ownership first.")
            await store.remove(workspace_id, current_user.id)
    
    # =========================================================================
    # Invites
    # =========================================================================
    
    @router.post("/workspaces/{workspace_id}/invites", response_model=InviteResponse)
    async def create_invite(
        workspace_id: str,
        data: InviteCreate,
        current_user: UserIdentity = Depends(require_workspace_admin),
    ):
        """Create an invite (admin only). Sends invite email if configured."""
        if data.role not in ("admin", "member"):
            raise HTTPException(status_code=400, detail="Role must be 'admin' or 'member'")
        
        async with get_db_connection() as conn:
            # Check if email is already a member
            member_store = MemberStore(conn)
            # Would need to lookup user by email - skip for now
            
            # Get workspace name for email
            ws_store = WorkspaceStore(conn)
            workspace = await ws_store.get(workspace_id)
            workspace_name = workspace["name"] if workspace else "Unknown Workspace"
            
            invite_store = InviteStore(conn)
            invite = await invite_store.create(
                workspace_id=workspace_id,
                email=data.email,
                role=data.role,
                invited_by=current_user.id,
            )
            
            # Build invite URL
            invite_url = None
            if invite_base_url:
                invite_url = f"{invite_base_url}?token={invite['token']}"
                invite["invite_url"] = invite_url
            
            # Send invite email (non-blocking, don't fail if email fails)
            if invite_url:
                try:
                    from .email import send_invite_email
                    inviter_name = current_user.email or current_user.username or "A team member"
                    await send_invite_email(
                        to_email=data.email,
                        workspace_name=workspace_name,
                        inviter_name=inviter_name,
                        invite_url=invite_url,
                        role=data.role,
                    )
                except Exception:
                    pass  # Don't fail invite creation if email fails
            
            return invite
    
    @router.get("/workspaces/{workspace_id}/invites", response_model=List[InviteResponse])
    async def list_invites(
        workspace_id: str,
        status_filter: Optional[str] = Query(None, alias="status"),
        current_user: UserIdentity = Depends(require_workspace_admin),
    ):
        """List invites for workspace (admin only)."""
        async with get_db_connection() as conn:
            store = InviteStore(conn)
            invites = await store.list_for_workspace(workspace_id, status=status_filter)
            # Don't expose tokens in list
            for inv in invites:
                inv["token"] = None
            return invites
    
    @router.delete("/workspaces/{workspace_id}/invites/{invite_id}", status_code=204)
    async def cancel_invite(
        workspace_id: str,
        invite_id: str,
        current_user: UserIdentity = Depends(require_workspace_admin),
    ):
        """Cancel an invite (admin only)."""
        async with get_db_connection() as conn:
            store = InviteStore(conn)
            invite = await store.get(invite_id)
            if not invite or invite["workspace_id"] != workspace_id:
                raise HTTPException(status_code=404, detail="Invite not found")
            await store.cancel(invite_id)
    
    @router.post("/invites/accept", response_model=InviteAcceptResponse)
    async def accept_invite(
        token: str,
        current_user: UserIdentity = Depends(get_current_user),
    ):
        """Accept an invite using token."""
        async with get_db_connection() as conn:
            invite_store = InviteStore(conn)
            invite = await invite_store.get_by_token(token)
            
            if not invite:
                raise HTTPException(status_code=404, detail="Invite not found")
            
            if invite["status"] != "pending":
                raise HTTPException(status_code=400, detail=f"Invite is {invite['status']}")
            
            # Accept invite (adds user to workspace)
            result = await invite_store.accept(token, current_user.id)
            if not result:
                raise HTTPException(status_code=400, detail="Invite expired")
            
            # Get workspace name
            ws_store = WorkspaceStore(conn)
            workspace = await ws_store.get(invite["workspace_id"])
            
            return {
                "workspace_id": invite["workspace_id"],
                "workspace_name": workspace["name"] if workspace else "Unknown",
                "role": invite["role"],
            }
    
    @router.get("/invites/pending", response_model=List[InviteResponse])
    async def list_pending_invites(
        current_user: UserIdentity = Depends(get_current_user),
    ):
        """List pending invites for current user's email."""
        async with get_db_connection() as conn:
            store = InviteStore(conn)
            invites = await store.list_for_email(current_user.email)
            return invites
    
    # =========================================================================
    # Projects
    # =========================================================================
    
    @router.post("/workspaces/{workspace_id}/projects", response_model=ProjectResponse)
    async def create_project(
        workspace_id: str,
        data: ProjectCreate,
        current_user: UserIdentity = Depends(require_workspace_member),
    ):
        """Create a new project in workspace."""
        async with get_db_connection() as conn:
            store = ProjectStore(conn)
            try:
                project = await store.create(
                    workspace_id=workspace_id,
                    name=data.name,
                    slug=data.slug,
                    description=data.description,
                    settings=data.settings,
                    created_by=current_user.id,
                )
                return project
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
    
    @router.get("/workspaces/{workspace_id}/projects", response_model=List[ProjectResponse])
    async def list_projects(
        workspace_id: str,
        current_user: UserIdentity = Depends(require_workspace_member),
    ):
        """List all projects in workspace."""
        async with get_db_connection() as conn:
            store = ProjectStore(conn)
            return await store.list_for_workspace(workspace_id)
    
    @router.get("/workspaces/{workspace_id}/projects/{project_id}", response_model=ProjectResponse)
    async def get_project(
        workspace_id: str,
        project_id: str,
        current_user: UserIdentity = Depends(require_workspace_member),
    ):
        """Get project details."""
        async with get_db_connection() as conn:
            store = ProjectStore(conn)
            project = await store.get(project_id)
            if not project or project["workspace_id"] != workspace_id:
                raise HTTPException(status_code=404, detail="Project not found")
            return project
    
    @router.patch("/workspaces/{workspace_id}/projects/{project_id}", response_model=ProjectResponse)
    async def update_project(
        workspace_id: str,
        project_id: str,
        data: ProjectUpdate,
        current_user: UserIdentity = Depends(require_workspace_member),
    ):
        """Update project."""
        async with get_db_connection() as conn:
            store = ProjectStore(conn)
            
            # Verify project exists in workspace
            project = await store.get(project_id)
            if not project or project["workspace_id"] != workspace_id:
                raise HTTPException(status_code=404, detail="Project not found")
            
            updates = data.model_dump(exclude_unset=True)
            updated = await store.update(project_id, updates)
            return updated
    
    @router.delete("/workspaces/{workspace_id}/projects/{project_id}", status_code=204)
    async def delete_project(
        workspace_id: str,
        project_id: str,
        current_user: UserIdentity = Depends(require_workspace_admin),
    ):
        """Delete project (admin only)."""
        async with get_db_connection() as conn:
            store = ProjectStore(conn)
            
            # Verify project exists in workspace
            project = await store.get(project_id)
            if not project or project["workspace_id"] != workspace_id:
                raise HTTPException(status_code=404, detail="Project not found")
            
            await store.delete(project_id)
    
    return router
