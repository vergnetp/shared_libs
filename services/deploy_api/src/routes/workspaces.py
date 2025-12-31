"""
Workspace management routes.
"""

from fastapi import APIRouter, Depends, HTTPException, status

# Import from app_kernel using backend path
try:
    from backend.app_kernel.auth import get_current_user, UserIdentity
    from backend.app_kernel.access import require_workspace_member, require_workspace_owner
except ImportError:
    # Fallback for development - will need proper path setup
    UserIdentity = dict
    def get_current_user(): pass
    def require_workspace_member(): pass
    def require_workspace_owner(): pass

from ..schemas import (
    WorkspaceCreateRequest,
    WorkspaceAPIResponse,
    WorkspaceMemberAddRequest,
    WorkspaceMemberAPIResponse,
    ErrorResponse,
)
from ..deps import get_workspace_store


router = APIRouter(prefix="/workspaces", tags=["workspaces"])


# =============================================================================
# Workspace CRUD
# =============================================================================

@router.post(
    "",
    response_model=WorkspaceAPIResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace(
    data: WorkspaceCreateRequest,
    current_user: UserIdentity = Depends(get_current_user),
    workspace_store=Depends(get_workspace_store),
):
    """Create a new workspace. User becomes owner."""
    # Check if name already exists
    existing = await workspace_store.get_by_name(data.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Workspace '{data.name}' already exists",
        )
    
    workspace = await workspace_store.create(
        name=data.name,
        owner_id=current_user.id,
    )
    
    return WorkspaceAPIResponse(
        id=workspace["id"],
        name=workspace["name"],
        owner_id=workspace["owner_id"],
        plan=workspace["plan"],
        role="owner",
        created_at=workspace["created_at"],
    )


@router.get("", response_model=list[WorkspaceAPIResponse])
async def list_workspaces(
    current_user: UserIdentity = Depends(get_current_user),
    workspace_store=Depends(get_workspace_store),
):
    """List workspaces the current user is a member of."""
    workspaces = await workspace_store.list_for_user(current_user.id)
    
    return [
        WorkspaceAPIResponse(
            id=ws["id"],
            name=ws["name"],
            owner_id=ws["owner_id"],
            plan=ws["plan"],
            role=ws.get("role", "member"),
            created_at=ws["created_at"],
        )
        for ws in workspaces
    ]


@router.get(
    "/{workspace_id}",
    response_model=WorkspaceAPIResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_workspace(
    workspace_id: str,
    current_user: UserIdentity = Depends(require_workspace_member),
    workspace_store=Depends(get_workspace_store),
):
    """Get workspace details."""
    workspace = await workspace_store.get(workspace_id)
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    
    role = await workspace_store.get_role(current_user.id, workspace_id)
    
    return WorkspaceAPIResponse(
        id=workspace["id"],
        name=workspace["name"],
        owner_id=workspace["owner_id"],
        plan=workspace["plan"],
        role=role,
        created_at=workspace["created_at"],
    )


@router.delete(
    "/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse}},
)
async def delete_workspace(
    workspace_id: str,
    current_user: UserIdentity = Depends(require_workspace_owner),
    workspace_store=Depends(get_workspace_store),
):
    """Delete workspace (owner only)."""
    deleted = await workspace_store.delete(workspace_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")


# =============================================================================
# Membership Management
# =============================================================================

@router.post(
    "/{workspace_id}/members",
    response_model=WorkspaceMemberAPIResponse,
    status_code=status.HTTP_201_CREATED,
    responses={404: {"model": ErrorResponse}},
)
async def add_member(
    workspace_id: str,
    data: WorkspaceMemberAddRequest,
    current_user: UserIdentity = Depends(require_workspace_owner),
    workspace_store=Depends(get_workspace_store),
):
    """Add a member to workspace (owner only)."""
    # Check if already a member
    existing = await workspace_store.is_member(data.user_id, workspace_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member",
        )
    
    member = await workspace_store.add_member(
        workspace_id=workspace_id,
        user_id=data.user_id,
        role=data.role,
    )
    
    return WorkspaceMemberAPIResponse(
        user_id=member["user_id"],
        workspace_id=member["workspace_id"],
        role=member["role"],
        joined_at=member["joined_at"],
    )


@router.delete(
    "/{workspace_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse}},
)
async def remove_member(
    workspace_id: str,
    user_id: str,
    current_user: UserIdentity = Depends(require_workspace_owner),
    workspace_store=Depends(get_workspace_store),
):
    """Remove a member from workspace (owner only)."""
    # Can't remove owner
    workspace = await workspace_store.get(workspace_id)
    if workspace and workspace["owner_id"] == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove workspace owner",
        )
    
    removed = await workspace_store.remove_member(workspace_id, user_id)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
