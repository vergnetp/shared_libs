"""
WorkspaceMember CRUD routes - AUTO-GENERATED from manifest.yaml
DO NOT EDIT - changes will be overwritten on regenerate

For custom logic, create src/routes/workspace_member.py
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from ..schemas import WorkspaceMemberCreate, WorkspaceMemberUpdate, WorkspaceMemberResponse
from ..crud import EntityCRUD

# Import db dependency from src (allows customization)
from ...src.deps import db_connection

router = APIRouter(prefix="/workspace_members", tags=["workspace_members"])
crud = EntityCRUD("workspace_members", soft_delete=False)


@router.get("", response_model=list[WorkspaceMemberResponse])
async def list_workspace_members(
    db=Depends(db_connection),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    workspace_id: Optional[str] = None,
):
    """List workspace_members."""
    return await crud.list(db, skip=skip, limit=limit, workspace_id=workspace_id)


@router.post("", response_model=WorkspaceMemberResponse, status_code=201)
async def create_workspace_member(data: WorkspaceMemberCreate, db=Depends(db_connection)):
    """Create workspace_member."""
    return await crud.create(db, data)


@router.get("/{id}", response_model=WorkspaceMemberResponse)
async def get_workspace_member(id: str, db=Depends(db_connection)):
    """Get workspace_member by ID."""
    entity = await crud.get(db, id)
    if not entity:
        raise HTTPException(404, "WorkspaceMember not found")
    return entity


@router.patch("/{id}", response_model=WorkspaceMemberResponse)
async def update_workspace_member(id: str, data: WorkspaceMemberUpdate, db=Depends(db_connection)):
    """Update workspace_member."""
    entity = await crud.update(db, id, data)
    if not entity:
        raise HTTPException(404, "WorkspaceMember not found")
    return entity


@router.delete("/{id}", status_code=204)
async def delete_workspace_member(id: str, db=Depends(db_connection)):
    """Delete workspace_member."""
    await crud.delete(db, id)
