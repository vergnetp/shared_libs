"""
Thread CRUD routes - AUTO-GENERATED from manifest.yaml
DO NOT EDIT - changes will be overwritten on regenerate

For custom logic, create src/routes/thread.py
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from ..schemas import ThreadCreate, ThreadUpdate, ThreadResponse
from ..crud import EntityCRUD

# Import db dependency from src (allows customization)
from ...src.deps import db_connection

router = APIRouter(prefix="/threads", tags=["threads"])
crud = EntityCRUD("threads", soft_delete=True)


@router.get("", response_model=list[ThreadResponse])
async def list_threads(
    db=Depends(db_connection),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    workspace_id: Optional[str] = None,
):
    """List threads."""
    return await crud.list(db, skip=skip, limit=limit, workspace_id=workspace_id)


@router.post("", response_model=ThreadResponse, status_code=201)
async def create_thread(data: ThreadCreate, db=Depends(db_connection)):
    """Create thread."""
    return await crud.create(db, data)


@router.get("/{id}", response_model=ThreadResponse)
async def get_thread(id: str, db=Depends(db_connection)):
    """Get thread by ID."""
    entity = await crud.get(db, id)
    if not entity:
        raise HTTPException(404, "Thread not found")
    return entity


@router.patch("/{id}", response_model=ThreadResponse)
async def update_thread(id: str, data: ThreadUpdate, db=Depends(db_connection)):
    """Update thread."""
    entity = await crud.update(db, id, data)
    if not entity:
        raise HTTPException(404, "Thread not found")
    return entity


@router.delete("/{id}", status_code=204)
async def delete_thread(id: str, db=Depends(db_connection)):
    """Delete thread."""
    await crud.delete(db, id)
