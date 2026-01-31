"""Audit queries - read from admin_db."""

import json
from typing import Optional, Dict, Any, List


async def get_audit_logs(
    admin_db,
    app: Optional[str] = None,
    entity: Optional[str] = None,
    entity_id: Optional[str] = None,
    user_id: Optional[str] = None,
    action: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    Query audit logs from admin_db.
    
    Args:
        app: Filter by app name
        entity: Filter by entity type (table name)
        entity_id: Filter by specific entity ID
        user_id: Filter by user who made change
        action: Filter by action (create, update, delete)
        since: From timestamp (ISO format)
        until: To timestamp (ISO format)
        limit: Max results
        offset: Pagination offset
    """
    conditions = []
    params = []
    
    if app:
        conditions.append("[app] = ?")
        params.append(app)
    
    if entity:
        conditions.append("[entity] = ?")
        params.append(entity)
    
    if entity_id:
        conditions.append("[entity_id] = ?")
        params.append(entity_id)
    
    if user_id:
        conditions.append("[user_id] = ?")
        params.append(user_id)
    
    if action:
        conditions.append("[action] = ?")
        params.append(action)
    
    if since:
        conditions.append("[timestamp] >= ?")
        params.append(since)
    
    if until:
        conditions.append("[timestamp] <= ?")
        params.append(until)
    
    where_clause = " AND ".join(conditions) if conditions else None
    
    results = await admin_db.find_entities(
        "kernel_audit_logs",
        where_clause=where_clause,
        params=tuple(params) if params else None,
        order_by="[timestamp] DESC",
        limit=limit,
        offset=offset,
    )
    
    # Parse JSON fields
    logs = []
    for row in results:
        log = dict(row)
        for field in ("changes", "old_snapshot", "new_snapshot"):
            if log.get(field):
                try:
                    log[field] = json.loads(log[field])
                except:
                    pass
        logs.append(log)
    
    return logs


async def get_entity_audit_history(
    admin_db,
    entity: str,
    entity_id: str,
    app: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Get complete audit history for a specific entity."""
    return await get_audit_logs(
        admin_db,
        app=app,
        entity=entity,
        entity_id=entity_id,
        limit=limit,
    )


async def count_audit_logs(
    admin_db,
    app: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> int:
    """Count audit logs matching filters."""
    conditions = []
    params = []
    
    if app:
        conditions.append("[app] = ?")
        params.append(app)
    
    if since:
        conditions.append("[timestamp] >= ?")
        params.append(since)
    
    if until:
        conditions.append("[timestamp] <= ?")
        params.append(until)
    
    where_clause = " AND ".join(conditions) if conditions else None
    
    return await admin_db.count_entities(
        "kernel_audit_logs",
        where_clause=where_clause,
        params=tuple(params) if params else None,
    )
