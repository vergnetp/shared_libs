"""Audit log API routes."""

from typing import Dict, List, Optional, Any, Callable
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel


class AuditLogEntry(BaseModel):
    id: str
    workspace_id: Optional[str]
    user_id: Optional[str]
    action: str
    entity: Optional[str]
    entity_id: Optional[str]
    changes: Optional[Dict[str, Any]]
    metadata: Optional[Dict[str, Any]]
    ip: Optional[str]
    timestamp: str


class AuditLogResponse(BaseModel):
    logs: List[AuditLogEntry]
    total: Optional[int]


def create_audit_router(
    get_current_user: Callable,
    get_db_connection: Callable,
    prefix: str = "/audit",
    tags: List[str] = None,
    require_admin: bool = True,
    is_admin: Optional[Callable] = None,
) -> APIRouter:
    """
    Create audit log router.
    
    Endpoints:
        GET /audit               - Query audit logs
        GET /audit/entity/{type}/{id} - Get entity history
    """
    router = APIRouter(prefix=prefix, tags=tags or ["audit"])
    
    def _check_admin(user):
        if is_admin:
            return is_admin(user)
        role = getattr(user, "role", None)
        return role == "admin"
    
    @router.get("", response_model=AuditLogResponse)
    async def query_audit_logs(
        workspace_id: Optional[str] = None,
        user_id: Optional[str] = None,
        entity: Optional[str] = None,
        entity_id: Optional[str] = None,
        action: Optional[str] = Query(None, description="Action filter (use * suffix for prefix match)"),
        since: Optional[str] = Query(None, description="From date (ISO format)"),
        until: Optional[str] = Query(None, description="To date (ISO format)"),
        limit: int = Query(100, le=1000),
        offset: int = Query(0, ge=0),
        include_count: bool = False,
        user = Depends(get_current_user),
    ):
        """Query audit logs with filters."""
        from .stores import get_audit_logs, count_audit_logs
        
        # Check permissions
        if require_admin and not _check_admin(user):
            # Non-admins can only see their workspace
            workspace_id = getattr(user, "workspace_id", None)
            if not workspace_id:
                raise HTTPException(403, "Access denied")
        
        async with get_db_connection() as db:
            logs = await get_audit_logs(
                db,
                workspace_id=workspace_id,
                user_id=user_id,
                entity=entity,
                entity_id=entity_id,
                action=action,
                since=since,
                until=until,
                limit=limit,
                offset=offset,
            )
            
            total = None
            if include_count:
                total = await count_audit_logs(db, workspace_id=workspace_id, since=since, until=until)
        
        return AuditLogResponse(logs=logs, total=total)
    
    @router.get("/entity/{entity}/{entity_id}")
    async def get_entity_history(
        entity: str,
        entity_id: str,
        limit: int = Query(50, le=200),
        user = Depends(get_current_user),
    ):
        """Get complete audit history for a specific entity."""
        from .stores import get_entity_audit_history
        
        # Admins or allow if non-admin access is configured
        if require_admin and not _check_admin(user):
            raise HTTPException(403, "Access denied")
        
        async with get_db_connection() as db:
            logs = await get_entity_audit_history(db, entity, entity_id, limit=limit)
        
        return {"entity": entity, "entity_id": entity_id, "history": logs}
    
    return router
