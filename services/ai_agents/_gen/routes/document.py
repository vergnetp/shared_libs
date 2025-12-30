"""
Document CRUD routes - AUTO-GENERATED from manifest.yaml
DO NOT EDIT - changes will be overwritten on regenerate

For custom logic, create src/routes/document.py
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from ..schemas import DocumentCreate, DocumentUpdate, DocumentResponse
from ..crud import EntityCRUD

# Import db dependency from src (allows customization)
from ...src.deps import get_db

router = APIRouter(prefix="/documents", tags=["documents"])
crud = EntityCRUD("documents", soft_delete=True)


@router.get("", response_model=list[DocumentResponse])
async def list_documents(
    db=Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    workspace_id: Optional[str] = None,
):
    """List documents."""
    return await crud.list(db, skip=skip, limit=limit, workspace_id=workspace_id)


@router.post("", response_model=DocumentResponse, status_code=201)
async def create_document(data: DocumentCreate, db=Depends(get_db)):
    """Create document."""
    return await crud.create(db, data)


@router.get("/{id}", response_model=DocumentResponse)
async def get_document(id: str, db=Depends(get_db)):
    """Get document by ID."""
    entity = await crud.get(db, id)
    if not entity:
        raise HTTPException(404, "Document not found")
    return entity


@router.patch("/{id}", response_model=DocumentResponse)
async def update_document(id: str, data: DocumentUpdate, db=Depends(get_db)):
    """Update document."""
    entity = await crud.update(db, id, data)
    if not entity:
        raise HTTPException(404, "Document not found")
    return entity


@router.delete("/{id}", status_code=204)
async def delete_document(id: str, db=Depends(get_db)):
    """Delete document."""
    await crud.delete(db, id)
