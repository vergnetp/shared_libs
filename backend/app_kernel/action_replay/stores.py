"""Action replay storage — direct DB writes (low volume, no Redis queue needed)."""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any


TABLE = "kernel_action_replays"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def save_replay(
    db,
    error_message: str = None,
    error_source: str = None,
    url: str = None,
    user_agent: str = None,
    replay_log: str = None,
    user_id: str = None,
    workspace_id: str = None,
) -> str:
    """
    Save an action replay.
    
    Args:
        db: Database connection
        error_message: Error that triggered the save
        error_source: Where the error came from (js_error, api_5xx, unhandled_promise, etc.)
        url: Page URL when error occurred
        user_agent: Browser user agent
        replay_log: JSON string of action entries
        user_id: Who hit the error (optional — errors can happen before login)
        workspace_id: Which workspace (optional)
    
    Returns:
        Replay ID
    """
    replay_id = str(uuid.uuid4())
    now = _now_iso()
    
    await db.save_entity(TABLE, {
        "id": replay_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "error_message": (error_message or "")[:500],
        "error_source": (error_source or "")[:50],
        "url": (url or "")[:500],
        "user_agent": (user_agent or "")[:300],
        "replay_log": replay_log,
        "resolved": False,
        "timestamp": now,
        "created_at": now,
    })
    
    return replay_id


async def list_replays(
    db,
    workspace_id: str = None,
    resolved: bool = None,
    since: str = None,
    until: str = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    List action replays, newest first.
    
    Args:
        workspace_id: Filter by workspace
        resolved: Filter by resolved status
        since: From timestamp (ISO format)
        until: To timestamp (ISO format)
        limit: Max results (default 50)
        offset: Pagination offset
    """
    conditions = []
    params = []
    
    if workspace_id:
        conditions.append("[workspace_id] = ?")
        params.append(workspace_id)
    
    if resolved is not None:
        conditions.append("[resolved] = ?")
        params.append(resolved)
    
    if since:
        conditions.append("[timestamp] >= ?")
        params.append(since)
    
    if until:
        conditions.append("[timestamp] <= ?")
        params.append(until)
    
    where_clause = " AND ".join(conditions) if conditions else None
    
    results = await db.find_entities(
        TABLE,
        where_clause=where_clause,
        params=tuple(params) if params else None,
        order_by="[timestamp] DESC",
        limit=limit,
        offset=offset,
    )
    
    # Return summary (no replay_log — that's large, fetch individually)
    replays = []
    for row in results:
        r = dict(row)
        r.pop("replay_log", None)
        replays.append(r)
    
    return replays


async def get_replay(db, replay_id: str) -> Optional[Dict[str, Any]]:
    """Get full action replay including log entries."""
    results = await db.find_entities(
        TABLE,
        where_clause="[id] = ?",
        params=(replay_id,),
        limit=1,
    )
    if not results:
        return None
    
    r = dict(results[0])
    # Parse replay_log JSON
    if r.get("replay_log"):
        try:
            r["replay_log"] = json.loads(r["replay_log"])
        except Exception:
            pass
    return r


async def resolve_replay(db, replay_id: str) -> bool:
    """Mark an action replay as resolved."""
    await db.update_entity(TABLE, replay_id, {"resolved": True})
    return True


async def count_replays(
    db,
    workspace_id: str = None,
    resolved: bool = None,
) -> int:
    """Count replays matching filters."""
    conditions = []
    params = []
    
    if workspace_id:
        conditions.append("[workspace_id] = ?")
        params.append(workspace_id)
    
    if resolved is not None:
        conditions.append("[resolved] = ?")
        params.append(resolved)
    
    where_clause = " AND ".join(conditions) if conditions else None
    
    return await db.count_entities(
        TABLE,
        where_clause=where_clause,
        params=tuple(params) if params else None,
    )
