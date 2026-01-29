"""Metering event publisher - async via Redis."""

import json
from datetime import datetime, timezone
from typing import Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_period_key() -> str:
    """Get current month period key (YYYY-MM)."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


async def track_request(
    redis_client,
    app: str,
    user_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    endpoint: str = "",
    method: str = "",
    status_code: int = 0,
    latency_ms: int = 0,
    bytes_in: int = 0,
    bytes_out: int = 0,
) -> None:
    """
    Track an API request - push to Redis queue.
    Fire-and-forget - failures silently ignored.
    """
    try:
        event = {
            "type": "request",
            "app": app,
            "user_id": user_id,
            "workspace_id": workspace_id,
            "endpoint": endpoint,
            "method": method,
            "status_code": status_code,
            "latency_ms": latency_ms,
            "bytes_in": bytes_in,
            "bytes_out": bytes_out,
            "period": _get_period_key(),
            "timestamp": _now_iso(),
        }
        await redis_client.lpush("admin:metering_events", json.dumps(event))
    except Exception:
        pass  # Fire and forget


async def track_usage(
    redis_client,
    app: str,
    user_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    **metrics: int,
) -> None:
    """
    Track custom usage metrics - push to Redis queue.
    
    Example:
        await track_usage(redis, "deploy_api",
            user_id=user.id,
            workspace_id=ws_id,
            tokens=1500,
            deployments=1,
            ai_calls=1,
        )
    """
    if not metrics:
        return
    
    try:
        event = {
            "type": "custom",
            "app": app,
            "user_id": user_id,
            "workspace_id": workspace_id,
            "metrics": metrics,
            "period": _get_period_key(),
            "timestamp": _now_iso(),
        }
        await redis_client.lpush("admin:metering_events", json.dumps(event))
    except Exception:
        pass  # Fire and forget
