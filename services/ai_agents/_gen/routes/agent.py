"""
Agent CRUD routes - AUTO-GENERATED from manifest.yaml
DO NOT EDIT - changes will be overwritten on regenerate

For custom logic, create src/routes/agent.py
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from ..schemas import AgentCreate, AgentUpdate, AgentResponse
from ..crud import EntityCRUD

# Import db dependency from src (allows customization)
from ...src.deps import db_connection

router = APIRouter(prefix="/agents", tags=["agents"])
crud = EntityCRUD("agents", soft_delete=True)


@router.get("", response_model=list[AgentResponse])
async def list_agents(
    db=Depends(db_connection),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    workspace_id: Optional[str] = None,
):
    """List agents."""
    return await crud.list(db, skip=skip, limit=limit, workspace_id=workspace_id)


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(data: AgentCreate, db=Depends(db_connection)):
    """Create agent."""
    return await crud.create(db, data)


@router.get("/{id}", response_model=AgentResponse)
async def get_agent(id: str, db=Depends(db_connection)):
    """Get agent by ID."""
    entity = await crud.get(db, id)
    if not entity:
        raise HTTPException(404, "Agent not found")
    return entity


@router.patch("/{id}", response_model=AgentResponse)
async def update_agent(id: str, data: AgentUpdate, db=Depends(db_connection)):
    """Update agent."""
    entity = await crud.update(db, id, data)
    if not entity:
        raise HTTPException(404, "Agent not found")
    return entity


@router.delete("/{id}", status_code=204)
async def delete_agent(id: str, db=Depends(db_connection)):
    """Delete agent."""
    await crud.delete(db, id)
