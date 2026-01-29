"""Webhook storage."""

import json
import secrets
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_secret() -> str:
    """Generate a webhook signing secret."""
    return f"whsec_{secrets.token_urlsafe(32)}"


async def init_webhooks_schema(db) -> None:
    """Create webhooks tables."""
    # Webhook subscriptions
    await db.execute("""
        CREATE TABLE IF NOT EXISTS webhooks (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            url TEXT NOT NULL,
            events TEXT NOT NULL,
            secret TEXT,
            description TEXT,
            enabled INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_webhooks_workspace ON webhooks(workspace_id)")
    
    # Webhook delivery logs
    await db.execute("""
        CREATE TABLE IF NOT EXISTS webhook_deliveries (
            id TEXT PRIMARY KEY,
            webhook_id TEXT NOT NULL,
            event TEXT NOT NULL,
            payload TEXT,
            response_status INTEGER,
            response_body TEXT,
            duration_ms INTEGER,
            success INTEGER,
            error TEXT,
            created_at TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_webhook ON webhook_deliveries(webhook_id, created_at)")


async def create_webhook(
    db,
    workspace_id: str,
    url: str,
    events: List[str],
    secret: Optional[str] = None,
    description: Optional[str] = None,
    enabled: bool = True,
) -> Dict[str, Any]:
    """
    Create a new webhook subscription.
    
    Args:
        workspace_id: Workspace to receive events from
        url: URL to POST events to
        events: List of event types to subscribe to (e.g., ["deployment.*", "service.created"])
        secret: Optional signing secret (auto-generated if not provided)
        description: Human-readable description
        enabled: Whether webhook is active
    """
    import uuid
    
    now = _now_iso()
    webhook_id = str(uuid.uuid4())
    
    if not secret:
        secret = _generate_secret()
    
    await db.save_entity("webhooks", {
        "id": webhook_id,
        "workspace_id": workspace_id,
        "url": url,
        "events": json.dumps(events),
        "secret": secret,
        "description": description,
        "enabled": 1 if enabled else 0,
        "created_at": now,
        "updated_at": now,
    })
    
    return {
        "id": webhook_id,
        "workspace_id": workspace_id,
        "url": url,
        "events": events,
        "secret": secret,
        "description": description,
        "enabled": enabled,
        "created_at": now,
    }


async def get_webhook(
    db,
    webhook_id: str,
    workspace_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Get a webhook by ID."""
    where = "[id] = ?"
    params = [webhook_id]
    
    if workspace_id:
        where += " AND [workspace_id] = ?"
        params.append(workspace_id)
    
    results = await db.find_entities(
        "webhooks",
        where_clause=where,
        params=tuple(params),
        limit=1,
    )
    
    if not results:
        return None
    
    return _parse_webhook(results[0])


async def list_webhooks(
    db,
    workspace_id: str,
    include_disabled: bool = False,
) -> List[Dict[str, Any]]:
    """List webhooks for a workspace."""
    where = "[workspace_id] = ?"
    params = [workspace_id]
    
    if not include_disabled:
        where += " AND [enabled] = 1"
    
    results = await db.find_entities(
        "webhooks",
        where_clause=where,
        params=tuple(params),
        order_by="[created_at] DESC",
    )
    
    return [_parse_webhook(row, include_secret=False) for row in results]


async def update_webhook(
    db,
    webhook_id: str,
    workspace_id: str,
    url: Optional[str] = None,
    events: Optional[List[str]] = None,
    description: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    """Update a webhook."""
    webhook = await get_webhook(db, webhook_id, workspace_id)
    if not webhook:
        return None
    
    updates = {"id": webhook_id, "updated_at": _now_iso()}
    
    if url is not None:
        updates["url"] = url
    if events is not None:
        updates["events"] = json.dumps(events)
    if description is not None:
        updates["description"] = description
    if enabled is not None:
        updates["enabled"] = 1 if enabled else 0
    
    await db.save_entity("webhooks", updates)
    
    return await get_webhook(db, webhook_id, workspace_id)


async def delete_webhook(
    db,
    webhook_id: str,
    workspace_id: str,
) -> bool:
    """Delete a webhook."""
    webhook = await get_webhook(db, webhook_id, workspace_id)
    if not webhook:
        return False
    
    await db.delete_entity("webhooks", webhook_id, permanent=True)
    return True


async def get_webhooks_for_event(
    db,
    workspace_id: str,
    event: str,
) -> List[Dict[str, Any]]:
    """Get all enabled webhooks subscribed to an event."""
    webhooks = await list_webhooks(db, workspace_id, include_disabled=False)
    
    matching = []
    for webhook in webhooks:
        if _event_matches(event, webhook["events"]):
            # Get full webhook with secret for dispatching
            full_webhook = await get_webhook(db, webhook["id"])
            if full_webhook:
                matching.append(full_webhook)
    
    return matching


def _event_matches(event: str, subscribed_events: List[str]) -> bool:
    """Check if event matches any subscribed pattern."""
    for pattern in subscribed_events:
        if pattern == "*":
            return True
        if pattern == event:
            return True
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            if event.startswith(prefix + "."):
                return True
    return False


def _parse_webhook(row: Dict[str, Any], include_secret: bool = True) -> Dict[str, Any]:
    """Parse webhook from database row."""
    events = []
    if row.get("events"):
        try:
            events = json.loads(row["events"])
        except:
            pass
    
    webhook = {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "url": row["url"],
        "events": events,
        "description": row.get("description"),
        "enabled": bool(row.get("enabled")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }
    
    if include_secret:
        webhook["secret"] = row.get("secret")
    
    return webhook


async def log_delivery(
    db,
    webhook_id: str,
    event: str,
    payload: Dict[str, Any],
    response_status: Optional[int],
    response_body: Optional[str],
    duration_ms: int,
    success: bool,
    error: Optional[str] = None,
) -> None:
    """Log a webhook delivery attempt."""
    import uuid
    
    await db.save_entity("webhook_deliveries", {
        "id": str(uuid.uuid4()),
        "webhook_id": webhook_id,
        "event": event,
        "payload": json.dumps(payload),
        "response_status": response_status,
        "response_body": response_body[:1000] if response_body else None,
        "duration_ms": duration_ms,
        "success": 1 if success else 0,
        "error": error,
        "created_at": _now_iso(),
    })


async def get_delivery_logs(
    db,
    webhook_id: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Get delivery logs for a webhook."""
    results = await db.find_entities(
        "webhook_deliveries",
        where_clause="[webhook_id] = ?",
        params=(webhook_id,),
        order_by="[created_at] DESC",
        limit=limit,
    )
    
    logs = []
    for row in results:
        log = dict(row)
        if log.get("payload"):
            try:
                log["payload"] = json.loads(log["payload"])
            except:
                pass
        log["success"] = bool(log.get("success"))
        logs.append(log)
    
    return logs
