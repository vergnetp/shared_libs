"""
Workspace management routes.
"""
from fastapi import APIRouter, Depends, HTTPException, status

from backend.app_kernel.auth import get_current_user, UserIdentity

from ..schemas import (
    WorkspaceCreate,
    WorkspaceResponse,
    WorkspaceMemberAdd,
    WorkspaceMemberResponse,
    ErrorResponse,
)
from ..deps import get_workspace_store

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.post(
    "",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace(
    data: WorkspaceCreate,
    current_user: UserIdentity = Depends(get_current_user),
    workspace_store=Depends(get_workspace_store),
):
    """Create a new workspace (tenant)."""
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
    
    return WorkspaceResponse(
        id=workspace["id"],
        name=workspace["name"],
        owner_id=workspace["owner_id"],
        plan=workspace["plan"],
        created_at=workspace["created_at"],
    )


@router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces(
    current_user: UserIdentity = Depends(get_current_user),
    workspace_store=Depends(get_workspace_store),
):
    """List workspaces the current user belongs to."""
    workspaces = await workspace_store.list_for_user(current_user.id)
    
    return [
        WorkspaceResponse(
            id=ws["id"],
            name=ws["name"],
            owner_id=ws["owner_id"],
            plan=ws["plan"],
            created_at=ws["created_at"],
        )
        for ws in workspaces
    ]


@router.get(
    "/{workspace_id}",
    response_model=WorkspaceResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_workspace(
    workspace_id: str,
    current_user: UserIdentity = Depends(get_current_user),
    workspace_store=Depends(get_workspace_store),
):
    """Get workspace details."""
    # Check membership
    if not await workspace_store.is_member(current_user.id, workspace_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    
    workspace = await workspace_store.get(workspace_id)
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    
    return WorkspaceResponse(
        id=workspace["id"],
        name=workspace["name"],
        owner_id=workspace["owner_id"],
        plan=workspace["plan"],
        created_at=workspace["created_at"],
    )


@router.delete(
    "/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def delete_workspace(
    workspace_id: str,
    current_user: UserIdentity = Depends(get_current_user),
    workspace_store=Depends(get_workspace_store),
):
    """Delete a workspace. Owner only."""
    workspace = await workspace_store.get(workspace_id)
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    
    if workspace["owner_id"] != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only owner can delete workspace")
    
    await workspace_store.delete(workspace_id)


# =============================================================================
# Members
# =============================================================================

@router.post(
    "/{workspace_id}/members",
    response_model=WorkspaceMemberResponse,
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def add_member(
    workspace_id: str,
    data: WorkspaceMemberAdd,
    current_user: UserIdentity = Depends(get_current_user),
    workspace_store=Depends(get_workspace_store),
):
    """Add a member to workspace. Owner/admin only."""
    workspace = await workspace_store.get(workspace_id)
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    
    # Check if current user is owner or admin
    role = await workspace_store.get_role(current_user.id, workspace_id)
    if role not in ("owner", "admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    
    # Check if already a member
    if await workspace_store.is_member(data.user_id, workspace_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User is already a member")
    
    member = await workspace_store.add_member(
        workspace_id=workspace_id,
        user_id=data.user_id,
        role=data.role,
    )
    
    return WorkspaceMemberResponse(
        user_id=member["user_id"],
        workspace_id=member["workspace_id"],
        role=member["role"],
        joined_at=member["joined_at"],
    )


@router.delete(
    "/{workspace_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def remove_member(
    workspace_id: str,
    user_id: str,
    current_user: UserIdentity = Depends(get_current_user),
    workspace_store=Depends(get_workspace_store),
):
    """Remove a member from workspace. Owner/admin only, cannot remove owner."""
    workspace = await workspace_store.get(workspace_id)
    if not workspace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")
    
    # Cannot remove owner
    if workspace["owner_id"] == user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot remove workspace owner")
    
    # Check if current user is owner or admin
    role = await workspace_store.get_role(current_user.id, workspace_id)
    if role not in ("owner", "admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    
    await workspace_store.remove_member(workspace_id, user_id)
