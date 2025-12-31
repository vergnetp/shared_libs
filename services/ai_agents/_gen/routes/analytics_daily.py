"""
AnalyticsDaily CRUD routes - AUTO-GENERATED from manifest.yaml
DO NOT EDIT - changes will be overwritten on regenerate

For custom logic, create src/routes/analytics_daily.py
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from ..schemas import AnalyticsDailyCreate, AnalyticsDailyUpdate, AnalyticsDailyResponse
from ..crud import EntityCRUD

# Import db dependency from src (allows customization)
from ...src.deps import db_connection

router = APIRouter(prefix="/analytics_dailies", tags=["analytics_dailies"])
crud = EntityCRUD("analytics_dailies", soft_delete=False)


@router.get("", response_model=list[AnalyticsDailyResponse])
async def list_analytics_dailies(
    db=Depends(db_connection),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    workspace_id: Optional[str] = None,
):
    """List analytics_dailies."""
    return await crud.list(db, skip=skip, limit=limit, workspace_id=workspace_id)


@router.post("", response_model=AnalyticsDailyResponse, status_code=201)
async def create_analytics_daily(data: AnalyticsDailyCreate, db=Depends(db_connection)):
    """Create analytics_daily."""
    return await crud.create(db, data)


@router.get("/{id}", response_model=AnalyticsDailyResponse)
async def get_analytics_daily(id: str, db=Depends(db_connection)):
    """Get analytics_daily by ID."""
    entity = await crud.get(db, id)
    if not entity:
        raise HTTPException(404, "AnalyticsDaily not found")
    return entity


@router.patch("/{id}", response_model=AnalyticsDailyResponse)
async def update_analytics_daily(id: str, data: AnalyticsDailyUpdate, db=Depends(db_connection)):
    """Update analytics_daily."""
    entity = await crud.update(db, id, data)
    if not entity:
        raise HTTPException(404, "AnalyticsDaily not found")
    return entity


@router.delete("/{id}", status_code=204)
async def delete_analytics_daily(id: str, db=Depends(db_connection)):
    """Delete analytics_daily."""
    await crud.delete(db, id)
