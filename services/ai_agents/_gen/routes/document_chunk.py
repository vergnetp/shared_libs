"""
DocumentChunk CRUD routes - AUTO-GENERATED from manifest.yaml
DO NOT EDIT - changes will be overwritten on regenerate

For custom logic, create src/routes/document_chunk.py
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from ..schemas import DocumentChunkCreate, DocumentChunkUpdate, DocumentChunkResponse
from ..crud import EntityCRUD

# Import db dependency from src (allows customization)
from ...src.deps import db_connection

router = APIRouter(prefix="/document_chunks", tags=["document_chunks"])
crud = EntityCRUD("document_chunks", soft_delete=False)


@router.get("", response_model=list[DocumentChunkResponse])
async def list_document_chunks(
    db=Depends(db_connection),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    
):
    """List document_chunks."""
    return await crud.list(db, skip=skip, limit=limit)


@router.post("", response_model=DocumentChunkResponse, status_code=201)
async def create_document_chunk(data: DocumentChunkCreate, db=Depends(db_connection)):
    """Create document_chunk."""
    return await crud.create(db, data)


@router.get("/{id}", response_model=DocumentChunkResponse)
async def get_document_chunk(id: str, db=Depends(db_connection)):
    """Get document_chunk by ID."""
    entity = await crud.get(db, id)
    if not entity:
        raise HTTPException(404, "DocumentChunk not found")
    return entity


@router.patch("/{id}", response_model=DocumentChunkResponse)
async def update_document_chunk(id: str, data: DocumentChunkUpdate, db=Depends(db_connection)):
    """Update document_chunk."""
    entity = await crud.update(db, id, data)
    if not entity:
        raise HTTPException(404, "DocumentChunk not found")
    return entity


@router.delete("/{id}", status_code=204)
async def delete_document_chunk(id: str, db=Depends(db_connection)):
    """Delete document_chunk."""
    await crud.delete(db, id)
