"""Audit log API routes - reads from admin_db."""

from typing import Dict, List, Optional, Any, Callable
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel


class AuditLogEntry(BaseModel):
    id: str
    app: str
    entity: str
    entity_id: str
    action: str
    changes: Optional[Dict[str, Any]]
    user_id: Optional[str]
    request_id: Optional[str]
    timestamp: str


class AuditLogResponse(BaseModel):
    logs: List[AuditLogEntry]
    total: Optional[int]


def create_audit_router(
    get_current_user: Callable,
    get_admin_db_connection: Callable,
    app_name: str,
    prefix: str = "/audit",
    tags: List[str] = None,
    require_admin: bool = True,
    is_admin: Optional[Callable] = None,
) -> APIRouter:
    """
    Create audit log router (reads from admin_db).
    
    Endpoints:
        GET /audit               - Query audit logs for this app
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
        entity: Optional[str] = None,
        entity_id: Optional[str] = None,
        user_id: Optional[str] = None,
        action: Optional[str] = None,
        since: Optional[str] = Query(None, description="From timestamp (ISO format)"),
        until: Optional[str] = Query(None, description="To timestamp (ISO format)"),
        limit: int = Query(100, le=1000),
        offset: int = Query(0, ge=0),
        include_count: bool = False,
        user = Depends(get_current_user),
    ):
        """Query audit logs for this app."""
        from .queries import get_audit_logs, count_audit_logs
        
        if require_admin and not _check_admin(user):
            raise HTTPException(403, "Admin access required")
        
        async with get_admin_db_connection() as admin_db:
            logs = await get_audit_logs(
                admin_db,
                app=app_name,
                entity=entity,
                entity_id=entity_id,
                user_id=user_id,
                action=action,
                since=since,
                until=until,
                limit=limit,
                offset=offset,
            )
            
            total = None
            if include_count:
                total = await count_audit_logs(admin_db, app=app_name, since=since, until=until)
        
        return AuditLogResponse(logs=logs, total=total)
    
    @router.get("/entity/{entity}/{entity_id}")
    async def get_entity_history(
        entity: str,
        entity_id: str,
        limit: int = Query(50, le=200),
        user = Depends(get_current_user),
    ):
        """Get complete audit history for a specific entity."""
        from .queries import get_entity_audit_history
        
        if require_admin and not _check_admin(user):
            raise HTTPException(403, "Admin access required")
        
        async with get_admin_db_connection() as admin_db:
            logs = await get_entity_audit_history(
                admin_db, 
                entity, 
                entity_id, 
                app=app_name,
                limit=limit,
            )
        
        return {"entity": entity, "entity_id": entity_id, "history": logs}
    
    return router
