"""
Webhooks - Notify external systems on events.

All events are sent to all registered webhooks. Receiver decides
which events to handle based on the 'event' field in payload.

Usage:
    # Register webhook (receives ALL events)
    webhook = await create_webhook(db, workspace_id,
        url="https://slack.com/webhook/xxx",
        secret="optional-secret-for-signing",
    )
    
    # Trigger webhook (call from your code when events happen)
    # Sent to ALL webhooks for this workspace
    await trigger_webhook_event(db, workspace_id,
        event="deployment.succeeded",
        data={"service": "api", "version": 42},
    )
    # Payload: {"event": "deployment.succeeded", "data": {...}, "timestamp": "..."}
    
    # List webhooks
    webhooks = await list_webhooks(db, workspace_id)
    
    # Delete webhook
    await delete_webhook(db, webhook_id, workspace_id)
"""

from .stores import (
    create_webhook,
    get_webhook,
    list_webhooks,
    update_webhook,
    delete_webhook,
    init_webhooks_schema,
)
from .dispatcher import (
    trigger_webhook_event,
    dispatch_webhook,
    WebhookDelivery,
)
from .router import create_webhooks_router

__all__ = [
    # Stores
    "create_webhook",
    "get_webhook",
    "list_webhooks",
    "update_webhook",
    "delete_webhook",
    "init_webhooks_schema",
    # Dispatcher
    "trigger_webhook_event",
    "dispatch_webhook",
    "WebhookDelivery",
    # Router
    "create_webhooks_router",
]
