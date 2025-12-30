"""
UserContext CRUD routes - AUTO-GENERATED from manifest.yaml
DO NOT EDIT - changes will be overwritten on regenerate

For custom logic, create src/routes/user_context.py
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from ..schemas import UserContextCreate, UserContextUpdate, UserContextResponse
from ..crud import EntityCRUD

# Import db dependency from src (allows customization)
from ...src.deps import get_db

router = APIRouter(prefix="/user_contexts", tags=["user_contexts"])
crud = EntityCRUD("user_contexts", soft_delete=False)


@router.get("", response_model=list[UserContextResponse])
async def list_user_contexts(
    db=Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    workspace_id: Optional[str] = None,
):
    """List user_contexts."""
    return await crud.list(db, skip=skip, limit=limit, workspace_id=workspace_id)


@router.post("", response_model=UserContextResponse, status_code=201)
async def create_user_context(data: UserContextCreate, db=Depends(get_db)):
    """Create user_context."""
    return await crud.create(db, data)


@router.get("/{id}", response_model=UserContextResponse)
async def get_user_context(id: str, db=Depends(get_db)):
    """Get user_context by ID."""
    entity = await crud.get(db, id)
    if not entity:
        raise HTTPException(404, "UserContext not found")
    return entity


@router.patch("/{id}", response_model=UserContextResponse)
async def update_user_context(id: str, data: UserContextUpdate, db=Depends(get_db)):
    """Update user_context."""
    entity = await crud.update(db, id, data)
    if not entity:
        raise HTTPException(404, "UserContext not found")
    return entity


@router.delete("/{id}", status_code=204)
async def delete_user_context(id: str, db=Depends(get_db)):
    """Delete user_context."""
    await crud.delete(db, id)
