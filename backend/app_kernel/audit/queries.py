"""Audit queries - read from kernel_audit_logs."""

import json
from typing import Optional, Dict, Any, List


async def get_audit_logs(
    db,
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
    Query audit logs.
    
    Args:
        app: Ignored (kept for API compat, each app has own DB)
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
        for field in ("changes", "old_snapshot", "new_snapshot"):
            if log.get(field):
                try:
                    log[field] = json.loads(log[field])
                except Exception:
                    pass
        logs.append(log)
    
    return logs


async def get_entity_audit_history(
    db,
    entity: str,
    entity_id: str,
    app: Optional[str] = None,
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
    app: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> int:
    """Count audit logs matching filters."""
    conditions = []
    params = []
    
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


def compute_changes(
    old: Optional[Dict[str, Any]],
    new: Optional[Dict[str, Any]],
) -> Dict[str, list]:
    """
    Compute field-level changes between two entity snapshots.
    
    Returns dict of {field: [old_value, new_value]} for changed fields.
    Used at read time to compute diffs from history versions.
    """
    if not old and not new:
        return {}
    if not old:
        return {k: [None, v] for k, v in (new or {}).items()}
    if not new:
        return {k: [v, None] for k, v in (old or {}).items()}
    
    changes = {}
    for key in set(old.keys()) | set(new.keys()):
        old_val = old.get(key)
        new_val = new.get(key)
        if old_val != new_val:
            changes[key] = [old_val, new_val]
    return changes


# Fields that change on every save — not useful in diffs
_DIFF_SKIP_FIELDS = frozenset({
    "updated_at", "updated_by", "_version",
    "version", "history_timestamp", "history_user_id", "history_comment",
})


async def get_entity_version_diffs(
    db,
    entity: str,
    entity_id: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Compute diffs between consecutive versions of an entity from its _history table.
    
    This is the on-demand replacement for pre-computed audit diffs.
    Called when an admin wants to see what changed for a specific entity.
    
    Returns list of:
        {version, timestamp, user_id, comment, changes: {field: [old, new]}}
    """
    # Get version history (ordered by version ASC for pairwise comparison)
    history_table = f"{entity}_history"
    
    try:
        versions = await db.find_entities(
            history_table,
            where_clause="[id] = ?",
            params=(entity_id,),
            order_by="[version] ASC",
            limit=limit + 1,  # +1 to have a "before" for the first diff
        )
    except Exception:
        return []  # Table doesn't exist or query failed
    
    if not versions:
        return []
    
    diffs = []
    for i in range(len(versions)):
        current = versions[i]
        previous = versions[i - 1] if i > 0 else None
        
        raw_changes = compute_changes(previous, current)
        
        # Filter out noise fields
        meaningful = {k: v for k, v in raw_changes.items() if k not in _DIFF_SKIP_FIELDS}
        
        diffs.append({
            "version": current.get("version"),
            "timestamp": current.get("history_timestamp"),
            "user_id": current.get("history_user_id"),
            "comment": current.get("history_comment"),
            "action": "create" if previous is None else "update",
            "changes": meaningful,
        })
    
    return diffs
