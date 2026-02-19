"""Audit log storage and queries."""

from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import json


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def audit_log(
    db,
    action: str,
    user_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    entity: Optional[str] = None,
    entity_id: Optional[str] = None,
    changes: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> str:
    """
    Create an audit log entry.
    
    Args:
        action: What happened (e.g., "deployment.created", "user.login", "settings.updated")
        user_id: Who did it
        workspace_id: Which workspace
        entity: Entity type (e.g., "deployments", "users")
        entity_id: Specific entity ID
        changes: Dict of field -> [old_value, new_value] for updates
        metadata: Additional context
        ip: Client IP address
        user_agent: Client user agent
    
    Returns:
        Audit log entry ID
    """
    import uuid
    
    now = _now_iso()
    log_id = str(uuid.uuid4())
    
    await db.save_entity("kernel_audit_logs", {
        "id": log_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "action": action,
        "entity": entity,
        "entity_id": entity_id,
        "changes": json.dumps(changes) if changes else None,
        "metadata": json.dumps(metadata) if metadata else None,
        "ip": ip,
        "user_agent": user_agent,
        "timestamp": now,
        "created_at": now,
    })
    
    return log_id


async def get_audit_logs(
    db,
    workspace_id: Optional[str] = None,
    user_id: Optional[str] = None,
    entity: Optional[str] = None,
    entity_id: Optional[str] = None,
    action: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    Query audit logs with filters.
    
    Args:
        workspace_id: Filter by workspace
        user_id: Filter by user
        entity: Filter by entity type
        entity_id: Filter by specific entity
        action: Filter by action (supports prefix match with *)
        since: Filter from date (ISO format)
        until: Filter to date (ISO format)
        limit: Max results
        offset: Pagination offset
    """
    conditions = []
    params = []
    
    if workspace_id:
        conditions.append("[workspace_id] = ?")
        params.append(workspace_id)
    
    if user_id:
        conditions.append("[user_id] = ?")
        params.append(user_id)
    
    if entity:
        conditions.append("[entity] = ?")
        params.append(entity)
    
    if entity_id:
        conditions.append("[entity_id] = ?")
        params.append(entity_id)
    
    if action:
        if action.endswith("*"):
            conditions.append("[action] LIKE ?")
            params.append(action[:-1] + "%")
        else:
            conditions.append("[action] = ?")
            params.append(action)
    
    if since:
        conditions.append("[timestamp] >= ?")
        params.append(since)
    
    if until:
        conditions.append("[timestamp] <= ?")
        params.append(until)
    
    where_clause = " AND ".join(conditions) if conditions else None
    
    results = await db.find_entities(
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
        if log.get("changes"):
            try:
                log["changes"] = json.loads(log["changes"])
            except Exception:
                pass
        if log.get("metadata"):
            try:
                log["metadata"] = json.loads(log["metadata"])
            except Exception:
                pass
        logs.append(log)
    
    return logs


async def get_entity_audit_history(
    db,
    entity: str,
    entity_id: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Get complete audit history for a specific entity."""
    return await get_audit_logs(
        db,
        entity=entity,
        entity_id=entity_id,
        limit=limit,
    )


async def count_audit_logs(
    db,
    workspace_id: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> int:
    """Count audit logs matching filters."""
    conditions = []
    params = []
    
    if workspace_id:
        conditions.append("[workspace_id] = ?")
        params.append(workspace_id)
    
    if since:
        conditions.append("[timestamp] >= ?")
        params.append(since)
    
    if until:
        conditions.append("[timestamp] <= ?")
        params.append(until)
    
    where_clause = " AND ".join(conditions) if conditions else None
    
    return await db.count_entities(
        "kernel_audit_logs",
        where_clause=where_clause,
        params=tuple(params) if params else None,
    )
