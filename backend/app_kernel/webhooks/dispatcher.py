"""Webhook event dispatcher."""

import hmac
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
import httpx


@dataclass
class WebhookDelivery:
    """Result of a webhook delivery attempt."""
    webhook_id: str
    event: str
    success: bool
    status_code: Optional[int] = None
    duration_ms: int = 0
    error: Optional[str] = None


def _sign_payload(payload: str, secret: str) -> str:
    """Sign a payload with HMAC-SHA256."""
    return hmac.new(
        secret.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()


async def dispatch_webhook(
    url: str,
    event: str,
    data: Dict[str, Any],
    secret: Optional[str] = None,
    webhook_id: Optional[str] = None,
    timeout: float = 30.0,
) -> WebhookDelivery:
    """
    Send a webhook to a URL.
    
    Payload format:
    {
        "event": "deployment.succeeded",
        "data": {...},
        "timestamp": "2025-01-28T19:30:00Z",
        "webhook_id": "..."
    }
    
    Headers:
        X-Webhook-Event: deployment.succeeded
        X-Webhook-Signature: sha256=<signature>
        X-Webhook-Timestamp: <unix timestamp>
    """
    timestamp = int(time.time())
    
    payload = {
        "event": event,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "webhook_id": webhook_id,
    }
    
    payload_str = json.dumps(payload, sort_keys=True)
    
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Event": event,
        "X-Webhook-Timestamp": str(timestamp),
    }
    
    # Sign if secret provided
    if secret:
        signature = _sign_payload(f"{timestamp}.{payload_str}", secret)
        headers["X-Webhook-Signature"] = f"sha256={signature}"
    
    start = time.time()
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                content=payload_str,
                headers=headers,
                timeout=timeout,
            )
        
        duration_ms = int((time.time() - start) * 1000)
        
        # 2xx = success
        success = 200 <= response.status_code < 300
        
        return WebhookDelivery(
            webhook_id=webhook_id or "",
            event=event,
            success=success,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
    
    except httpx.TimeoutException:
        duration_ms = int((time.time() - start) * 1000)
        return WebhookDelivery(
            webhook_id=webhook_id or "",
            event=event,
            success=False,
            duration_ms=duration_ms,
            error="Timeout",
        )
    
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        return WebhookDelivery(
            webhook_id=webhook_id or "",
            event=event,
            success=False,
            duration_ms=duration_ms,
            error=str(e),
        )


async def trigger_webhook_event(
    db,
    workspace_id: str,
    event: str,
    data: Dict[str, Any],
    log_deliveries: bool = True,
) -> List[WebhookDelivery]:
    """
    Trigger an event for all webhooks in a workspace.
    
    Sends to ALL enabled webhooks concurrently. Receiver decides which 
    events to handle based on the event field in the payload.
    
    Args:
        db: Database connection
        workspace_id: Workspace that owns the event
        event: Event type (e.g., "deployment.succeeded")
        data: Event data
        log_deliveries: Whether to log delivery attempts
    
    Returns:
        List of delivery results
    """
    from .stores import get_webhooks_for_workspace, log_delivery
    
    webhooks = await get_webhooks_for_workspace(db, workspace_id)
    
    if not webhooks:
        return []
    
    # Dispatch all webhooks concurrently (not sequentially)
    import asyncio
    tasks = [
        dispatch_webhook(
            url=webhook["url"],
            event=event,
            data=data,
            secret=webhook.get("secret"),
            webhook_id=webhook["id"],
        )
        for webhook in webhooks
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Convert exceptions to failed deliveries and batch log
    deliveries = []
    log_entities = []
    for webhook, result in zip(webhooks, results):
        if isinstance(result, Exception):
            result = WebhookDelivery(
                webhook_id=webhook["id"],
                event=event,
                success=False,
                error=str(result),
            )
        deliveries.append(result)
        
        if log_deliveries:
            import json, uuid
            log_entities.append({
                "id": str(uuid.uuid4()),
                "webhook_id": webhook["id"],
                "event": event,
                "payload": json.dumps(data),
                "response_status": result.status_code,
                "response_body": None,
                "duration_ms": result.duration_ms,
                "success": 1 if result.success else 0,
                "error": result.error,
                "created_at": _now_iso(),
            })
    
    # Batch log all deliveries in one call
    if log_entities:
        await db.save_entities("kernel_webhook_deliveries", log_entities)
    
    return deliveries


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def verify_webhook_signature(
    payload: str,
    signature_header: str,
    secret: str,
    timestamp_header: Optional[str] = None,
    tolerance_seconds: int = 300,
) -> bool:
    """
    Verify a webhook signature (for receiving webhooks).
    
    Args:
        payload: Raw request body
        signature_header: X-Webhook-Signature header value
        secret: Webhook secret
        timestamp_header: X-Webhook-Timestamp header value (optional)
        tolerance_seconds: Max age of webhook in seconds
    
    Returns:
        True if signature is valid
    """
    if not signature_header.startswith("sha256="):
        return False
    
    received_sig = signature_header[7:]  # Remove "sha256=" prefix
    
    # Include timestamp in signature verification if provided
    if timestamp_header:
        try:
            timestamp = int(timestamp_header)
            # Check timestamp is recent
            now = int(time.time())
            if abs(now - timestamp) > tolerance_seconds:
                return False
            
            expected_sig = _sign_payload(f"{timestamp}.{payload}", secret)
        except (ValueError, TypeError):
            return False
    else:
        expected_sig = _sign_payload(payload, secret)
    
    return hmac.compare_digest(expected_sig, received_sig)
