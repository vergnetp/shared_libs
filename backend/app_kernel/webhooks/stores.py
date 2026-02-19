"""Webhook storage - simplified without event filtering."""

import json
import secrets
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_secret() -> str:
    """Generate a webhook signing secret."""
    return f"whsec_{secrets.token_urlsafe(32)}"


async def create_webhook(
    db,
    workspace_id: str,
    url: str,
    secret: Optional[str] = None,
    description: Optional[str] = None,
    enabled: bool = True,
) -> Dict[str, Any]:
    """
    Create a new webhook subscription.
    
    All events for the workspace will be sent to this URL.
    Receiver decides which events to handle based on payload.
    
    Args:
        workspace_id: Workspace to receive events from
        url: URL to POST events to
        secret: Optional signing secret (auto-generated if not provided)
        description: Human-readable description
        enabled: Whether webhook is active
    """
    import uuid
    
    now = _now_iso()
    webhook_id = str(uuid.uuid4())
    
    if not secret:
        secret = _generate_secret()
    
    await db.save_entity("kernel_webhooks", {
        "id": webhook_id,
        "workspace_id": workspace_id,
        "url": url,
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
        "kernel_webhooks",
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
        "kernel_webhooks",
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
    if description is not None:
        updates["description"] = description
    if enabled is not None:
        updates["enabled"] = 1 if enabled else 0
    
    await db.save_entity("kernel_webhooks", updates)
    
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
    
    await db.delete_entity("kernel_webhooks", webhook_id, permanent=True)
    return True


async def get_webhooks_for_workspace(
    db,
    workspace_id: str,
) -> List[Dict[str, Any]]:
    """Get all enabled webhooks for a workspace (with secrets for dispatching)."""
    results = await db.find_entities(
        "kernel_webhooks",
        where_clause="[workspace_id] = ? AND [enabled] = 1",
        params=(workspace_id,),
    )
    
    return [_parse_webhook(row, include_secret=True) for row in results]


def _parse_webhook(row: Dict[str, Any], include_secret: bool = True) -> Dict[str, Any]:
    """Parse webhook from database row."""
    webhook = {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "url": row["url"],
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
    
    await db.save_entity("kernel_webhook_deliveries", {
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
        "kernel_webhook_deliveries",
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
            except Exception:
                pass
        log["success"] = bool(log.get("success"))
        logs.append(log)
    
    return logs
