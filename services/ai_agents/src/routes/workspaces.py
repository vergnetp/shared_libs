"""
Workspace management endpoints.

Workspaces are the sharing boundary for resources (threads, documents, agents).
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

from ..deps import get_db, WorkspaceStore
from ..auth import get_current_user, CurrentUser
from ..authz import get_or_create_default_workspace


router = APIRouter(prefix="/workspaces", tags=["workspaces"])


# =============================================================================
# Schemas
# =============================================================================

class WorkspaceCreate(BaseModel):
    """Create a new workspace."""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class WorkspaceUpdate(BaseModel):
    """Update workspace fields."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    metadata: Optional[dict] = None


class WorkspaceResponse(BaseModel):
    """Workspace response."""
    id: str
    name: str
    description: Optional[str]
    metadata: dict
    created_at: datetime
    updated_at: Optional[datetime]


class MemberAdd(BaseModel):
    """Add a member to workspace."""
    user_id: str
    role: str = Field("member", pattern="^(owner|member)$")


class MemberResponse(BaseModel):
    """Workspace member response."""
    user_id: str
    workspace_id: str
    role: str
    created_at: datetime


def _to_response(workspace: dict) -> WorkspaceResponse:
    """Convert workspace dict to response."""
    def parse_dt(val):
        if val is None:
            return None
        if isinstance(val, datetime):
            return val
        if isinstance(val, str):
            try:
                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except:
                return None
        return None
    
    return WorkspaceResponse(
        id=workspace.get("id", ""),
        name=workspace.get("name", ""),
        description=workspace.get("description"),
        metadata=workspace.get("metadata") or {},
        created_at=parse_dt(workspace.get("created_at")) or datetime.utcnow(),
        updated_at=parse_dt(workspace.get("updated_at")),
    )


def _member_to_response(member: dict) -> MemberResponse:
    """Convert member dict to response."""
    def parse_dt(val):
        if val is None:
            return datetime.utcnow()
        if isinstance(val, datetime):
            return val
        if isinstance(val, str):
            try:
                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except:
                return datetime.utcnow()
        return datetime.utcnow()
    
    return MemberResponse(
        user_id=member.get("user_id", ""),
        workspace_id=member.get("workspace_id", ""),
        role=member.get("role", "member"),
        created_at=parse_dt(member.get("created_at")),
    )


# =============================================================================
# Workspace CRUD
# =============================================================================

@router.post(
    "",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace(
    data: WorkspaceCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Create a new workspace.
    
    The creating user automatically becomes the workspace owner.
    """
    store = WorkspaceStore(db)
    workspace = await store.create(
        name=data.name,
        user=current_user,
        description=data.description,
        metadata=data.metadata,
    )
    
    return _to_response(workspace)


@router.get(
    "",
    response_model=List[WorkspaceResponse],
)
async def list_workspaces(
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    List workspaces the current user is a member of.
    
    Admins see all workspaces.
    """
    store = WorkspaceStore(db)
    workspaces = await store.list(user=current_user)
    return [_to_response(w) for w in workspaces]


@router.get(
    "/default",
    response_model=WorkspaceResponse,
)
async def get_default_workspace(
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Get or create the user's default workspace.
    
    Useful for single-user scenarios or getting started.
    """
    workspace = await get_or_create_default_workspace(db, current_user)
    
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get default workspace",
        )
    
    return _to_response(workspace)


@router.get(
    "/{workspace_id}",
    response_model=WorkspaceResponse,
)
async def get_workspace(
    workspace_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get workspace by ID. Requires membership."""
    store = WorkspaceStore(db)
    workspace = await store.get(workspace_id, user=current_user)
    
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace not found: {workspace_id}",
        )
    
    return _to_response(workspace)


# =============================================================================
# Membership Management
# =============================================================================

@router.get(
    "/{workspace_id}/members",
    response_model=List[MemberResponse],
)
async def list_members(
    workspace_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """List workspace members. Requires membership."""
    store = WorkspaceStore(db)
    members = await store.get_members(workspace_id, user=current_user)
    
    if not members and not current_user.is_admin:
        # Could be empty workspace or no access - check
        workspace = await store.get(workspace_id, user=current_user)
        if not workspace:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workspace not found: {workspace_id}",
            )
    
    return [_member_to_response(m) for m in members]


@router.post(
    "/{workspace_id}/members",
    response_model=MemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
    workspace_id: str,
    data: MemberAdd,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Add a member to workspace.
    
    Requires workspace owner or admin.
    """
    store = WorkspaceStore(db)
    member = await store.add_member(
        workspace_id,
        data.user_id,
        data.role,
        user=current_user,
    )
    
    if not member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to add members to this workspace",
        )
    
    return _member_to_response(member)


@router.delete(
    "/{workspace_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member(
    workspace_id: str,
    user_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Remove a member from workspace.
    
    Requires workspace owner or admin.
    """
    store = WorkspaceStore(db)
    removed = await store.remove_member(workspace_id, user_id, user=current_user)
    
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to remove members from this workspace",
        )
