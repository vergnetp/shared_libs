"""
Webhooks - Notify external systems on events.

Usage:
    # Register webhook
    webhook = await create_webhook(db, workspace_id,
        url="https://slack.com/webhook/xxx",
        events=["deployment.succeeded", "deployment.failed"],
        secret="optional-secret-for-signing",
    )
    
    # Trigger webhook (call from your code when events happen)
    await trigger_webhook_event(db, workspace_id,
        event="deployment.succeeded",
        data={"service": "api", "version": 42},
    )
    
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
