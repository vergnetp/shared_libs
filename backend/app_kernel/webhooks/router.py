"""Webhook management routes."""

from typing import Dict, List, Optional, Any, Callable
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, HttpUrl


class WebhookCreate(BaseModel):
    url: str
    description: Optional[str] = None
    secret: Optional[str] = None


class WebhookUpdate(BaseModel):
    url: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None


class WebhookResponse(BaseModel):
    id: str
    workspace_id: str
    url: str
    description: Optional[str]
    enabled: bool
    created_at: Optional[str]
    updated_at: Optional[str]


class WebhookWithSecret(WebhookResponse):
    secret: str


class DeliveryLogResponse(BaseModel):
    id: str
    webhook_id: str
    event: str
    payload: Optional[Dict[str, Any]]
    response_status: Optional[int]
    duration_ms: int
    success: bool
    error: Optional[str]
    created_at: str


def create_webhooks_router(
    get_current_user: Callable,
    get_db_connection: Callable,
    prefix: str = "/webhooks",
    tags: List[str] = None,
) -> APIRouter:
    """
    Create webhooks management router.
    
    All events are sent to all registered webhooks. Receiver decides
    which events to handle based on the 'event' field in payload.
    
    Endpoints:
        POST   /webhooks                  - Create webhook
        GET    /webhooks                  - List webhooks
        GET    /webhooks/{id}             - Get webhook (with secret)
        PATCH  /webhooks/{id}             - Update webhook
        DELETE /webhooks/{id}             - Delete webhook
        GET    /webhooks/{id}/deliveries  - Get delivery logs
        POST   /webhooks/{id}/test        - Send test event
    """
    router = APIRouter(prefix=prefix, tags=tags or ["webhooks"])
    
    def _get_workspace_id(user) -> str:
        """Get workspace ID from user."""
        ws_id = getattr(user, "workspace_id", None)
        if not ws_id:
            raise HTTPException(400, "No workspace context")
        return ws_id
    
    @router.post("", response_model=WebhookWithSecret, status_code=201)
    async def create_new_webhook(
        data: WebhookCreate,
        user = Depends(get_current_user),
    ):
        """Create a new webhook subscription."""
        from .stores import create_webhook
        
        workspace_id = _get_workspace_id(user)
        
        async with get_db_connection() as db:
            return await create_webhook(
                db,
                workspace_id=workspace_id,
                url=data.url,
                secret=data.secret,
                description=data.description,
            )
    
    @router.get("", response_model=List[WebhookResponse])
    async def list_all_webhooks(
        include_disabled: bool = False,
        user = Depends(get_current_user),
    ):
        """List all webhooks for current workspace."""
        from .stores import list_webhooks
        
        workspace_id = _get_workspace_id(user)
        
        async with get_db_connection() as db:
            return await list_webhooks(db, workspace_id, include_disabled=include_disabled)
    
    @router.get("/{webhook_id}", response_model=WebhookWithSecret)
    async def get_webhook_details(
        webhook_id: str,
        user = Depends(get_current_user),
    ):
        """Get webhook details including secret."""
        from .stores import get_webhook
        
        workspace_id = _get_workspace_id(user)
        
        async with get_db_connection() as db:
            webhook = await get_webhook(db, webhook_id, workspace_id)
        
        if not webhook:
            raise HTTPException(404, "Webhook not found")
        
        return webhook
    
    @router.patch("/{webhook_id}", response_model=WebhookResponse)
    async def update_existing_webhook(
        webhook_id: str,
        data: WebhookUpdate,
        user = Depends(get_current_user),
    ):
        """Update a webhook."""
        from .stores import update_webhook
        
        workspace_id = _get_workspace_id(user)
        
        async with get_db_connection() as db:
            webhook = await update_webhook(
                db,
                webhook_id=webhook_id,
                workspace_id=workspace_id,
                url=data.url,
                description=data.description,
                enabled=data.enabled,
            )
        
        if not webhook:
            raise HTTPException(404, "Webhook not found")
        
        return webhook
    
    @router.delete("/{webhook_id}", status_code=204)
    async def remove_webhook(
        webhook_id: str,
        user = Depends(get_current_user),
    ):
        """Delete a webhook."""
        from .stores import delete_webhook
        
        workspace_id = _get_workspace_id(user)
        
        async with get_db_connection() as db:
            success = await delete_webhook(db, webhook_id, workspace_id)
        
        if not success:
            raise HTTPException(404, "Webhook not found")
    
    @router.get("/{webhook_id}/deliveries", response_model=List[DeliveryLogResponse])
    async def get_delivery_history(
        webhook_id: str,
        limit: int = 50,
        user = Depends(get_current_user),
    ):
        """Get webhook delivery logs."""
        from .stores import get_webhook, get_delivery_logs
        
        workspace_id = _get_workspace_id(user)
        
        async with get_db_connection() as db:
            # Verify ownership
            webhook = await get_webhook(db, webhook_id, workspace_id)
            if not webhook:
                raise HTTPException(404, "Webhook not found")
            
            return await get_delivery_logs(db, webhook_id, limit=limit)
    
    @router.post("/{webhook_id}/test")
    async def send_test_webhook(
        webhook_id: str,
        user = Depends(get_current_user),
    ):
        """Send a test event to the webhook."""
        from .stores import get_webhook
        from .dispatcher import dispatch_webhook
        
        workspace_id = _get_workspace_id(user)
        
        async with get_db_connection() as db:
            webhook = await get_webhook(db, webhook_id, workspace_id)
            if not webhook:
                raise HTTPException(404, "Webhook not found")
            
            result = await dispatch_webhook(
                url=webhook["url"],
                event="webhook.test",
                data={
                    "message": "This is a test webhook",
                    "workspace_id": workspace_id,
                    "triggered_by": getattr(user, "id", None),
                },
                secret=webhook.get("secret"),
                webhook_id=webhook_id,
            )
        
        return {
            "success": result.success,
            "status_code": result.status_code,
            "duration_ms": result.duration_ms,
            "error": result.error,
        }
    
    return router
