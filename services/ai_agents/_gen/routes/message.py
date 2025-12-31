"""
Message CRUD routes - AUTO-GENERATED from manifest.yaml
DO NOT EDIT - changes will be overwritten on regenerate

For custom logic, create src/routes/message.py
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from ..schemas import MessageCreate, MessageUpdate, MessageResponse
from ..crud import EntityCRUD

# Import db dependency from src (allows customization)
from ...src.deps import db_connection

router = APIRouter(prefix="/messages", tags=["messages"])
crud = EntityCRUD("messages", soft_delete=False)


@router.get("", response_model=list[MessageResponse])
async def list_messages(
    db=Depends(db_connection),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    
):
    """List messages."""
    return await crud.list(db, skip=skip, limit=limit)


@router.post("", response_model=MessageResponse, status_code=201)
async def create_message(data: MessageCreate, db=Depends(db_connection)):
    """Create message."""
    return await crud.create(db, data)


@router.get("/{id}", response_model=MessageResponse)
async def get_message(id: str, db=Depends(db_connection)):
    """Get message by ID."""
    entity = await crud.get(db, id)
    if not entity:
        raise HTTPException(404, "Message not found")
    return entity


@router.patch("/{id}", response_model=MessageResponse)
async def update_message(id: str, data: MessageUpdate, db=Depends(db_connection)):
    """Update message."""
    entity = await crud.update(db, id, data)
    if not entity:
        raise HTTPException(404, "Message not found")
    return entity


@router.delete("/{id}", status_code=204)
async def delete_message(id: str, db=Depends(db_connection)):
    """Delete message."""
    await crud.delete(db, id)
