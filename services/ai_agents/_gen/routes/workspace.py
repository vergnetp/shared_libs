"""
Workspace CRUD routes - AUTO-GENERATED from manifest.yaml
DO NOT EDIT - changes will be overwritten on regenerate

For custom logic, create src/routes/workspace.py
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from ..schemas import WorkspaceCreate, WorkspaceUpdate, WorkspaceResponse
from ..crud import EntityCRUD

# Import db dependency from src (allows customization)
from ...src.deps import db_connection

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
crud = EntityCRUD("workspaces", soft_delete=True)


@router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces(
    db=Depends(db_connection),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    
):
    """List workspaces."""
    return await crud.list(db, skip=skip, limit=limit)


@router.post("", response_model=WorkspaceResponse, status_code=201)
async def create_workspace(data: WorkspaceCreate, db=Depends(db_connection)):
    """Create workspace."""
    return await crud.create(db, data)


@router.get("/{id}", response_model=WorkspaceResponse)
async def get_workspace(id: str, db=Depends(db_connection)):
    """Get workspace by ID."""
    entity = await crud.get(db, id)
    if not entity:
        raise HTTPException(404, "Workspace not found")
    return entity


@router.patch("/{id}", response_model=WorkspaceResponse)
async def update_workspace(id: str, data: WorkspaceUpdate, db=Depends(db_connection)):
    """Update workspace."""
    entity = await crud.update(db, id, data)
    if not entity:
        raise HTTPException(404, "Workspace not found")
    return entity


@router.delete("/{id}", status_code=204)
async def delete_workspace(id: str, db=Depends(db_connection)):
    """Delete workspace."""
    await crud.delete(db, id)
