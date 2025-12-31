"""
Item CRUD routes - AUTO-GENERATED from manifest.yaml
DO NOT EDIT - changes will be overwritten on regenerate

For custom logic, create src/routes/item.py
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from ..schemas import ItemCreate, ItemUpdate, ItemResponse
from ..crud import EntityCRUD

# Import db dependency from src (allows customization)
from ...src.deps import get_db

router = APIRouter(prefix="/items", tags=["items"])
crud = EntityCRUD("items", soft_delete=True)


@router.get("", response_model=list[ItemResponse])
async def list_items(
    db=Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    workspace_id: Optional[str] = None,
):
    """List items."""
    return await crud.list(db, skip=skip, limit=limit, workspace_id=workspace_id)


@router.post("", response_model=ItemResponse, status_code=201)
async def create_item(data: ItemCreate, db=Depends(get_db)):
    """Create item."""
    return await crud.create(db, data)


@router.get("/{id}", response_model=ItemResponse)
async def get_item(id: str, db=Depends(get_db)):
    """Get item by ID."""
    entity = await crud.get(db, id)
    if not entity:
        raise HTTPException(404, "Item not found")
    return entity


@router.patch("/{id}", response_model=ItemResponse)
async def update_item(id: str, data: ItemUpdate, db=Depends(get_db)):
    """Update item."""
    entity = await crud.update(db, id, data)
    if not entity:
        raise HTTPException(404, "Item not found")
    return entity


@router.delete("/{id}", status_code=204)
async def delete_item(id: str, db=Depends(get_db)):
    """Delete item."""
    await crud.delete(db, id)
