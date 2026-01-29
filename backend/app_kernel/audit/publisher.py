"""Audit event publisher - async via Redis."""

import json
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Optional, Any, Dict


# Context vars set by kernel middleware
_current_user_id: ContextVar[Optional[str]] = ContextVar("audit_user_id", default=None)
_current_request_id: ContextVar[Optional[str]] = ContextVar("audit_request_id", default=None)
_current_app: ContextVar[Optional[str]] = ContextVar("audit_app", default=None)


def set_audit_context(
    app: Optional[str] = None,
    user_id: Optional[str] = None,
    request_id: Optional[str] = None,
):
    """Set audit context (called by kernel middleware)."""
    if app:
        _current_app.set(app)
    if user_id:
        _current_user_id.set(user_id)
    if request_id:
        _current_request_id.set(request_id)


def clear_audit_context():
    """Clear audit context (called after request)."""
    _current_app.set(None)
    _current_user_id.set(None)
    _current_request_id.set(None)


async def push_audit_event(
    redis_client,
    action: str,
    entity: str,
    entity_id: str,
    old: Optional[Dict[str, Any]],
    new: Optional[Dict[str, Any]],
    app: Optional[str] = None,
    user_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> None:
    """
    Push audit event to Redis queue.
    Fire-and-forget - failures are silently ignored.
    """
    try:
        # Compute changes (field: [old, new])
        changes = _compute_changes(old, new)
        
        event = {
            "app": app or _current_app.get(),
            "action": action,
            "entity": entity,
            "entity_id": entity_id,
            "changes": changes,
            "old_snapshot": old,
            "new_snapshot": new,
            "user_id": user_id or _current_user_id.get(),
            "request_id": request_id or _current_request_id.get(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        await redis_client.lpush("admin:audit_events", json.dumps(event))
    except Exception:
        pass  # Fire and forget


def _compute_changes(
    old: Optional[Dict[str, Any]],
    new: Optional[Dict[str, Any]],
) -> Dict[str, list]:
    """Compute field-level changes between old and new."""
    if not old and not new:
        return {}
    
    if not old:
        # Create - all fields are new
        return {k: [None, v] for k, v in (new or {}).items()}
    
    if not new:
        # Delete - all fields removed
        return {k: [v, None] for k, v in (old or {}).items()}
    
    # Update - find differences
    changes = {}
    all_keys = set(old.keys()) | set(new.keys())
    
    for key in all_keys:
        old_val = old.get(key)
        new_val = new.get(key)
        if old_val != new_val:
            changes[key] = [old_val, new_val]
    
    return changes


# Note: enable_audit() is no longer needed - audit is auto-enabled when REDIS_URL is set.
# The AuditWrappedConnection in db/session.py handles this automatically.
