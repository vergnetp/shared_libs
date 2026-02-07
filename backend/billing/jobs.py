"""
Billing Jobs - Background job processors for billing operations.

Integrates with app_kernel's job system.
Jobs get their own database connection via kernel's get_db_connection().

Usage in your service:
    from .jobs import BILLING_TASKS
    from ..app_kernel import create_service
    
    app = create_service(
        name="my-service",
        tasks={**MY_TASKS, **BILLING_TASKS},  # Merge billing tasks
        ...
    )

Queue jobs via kernel:
    from ..app_kernel import get_job_queue
    
    queue = get_job_queue()
    await queue.enqueue("billing.sync_subscription", {"subscription_id": "..."})
"""

from typing import Dict, Any, Optional
from .config import BillingConfig
from .services import BillingService
from .sync import StripeSync


# Global billing config - set at startup
_billing_config: Optional[BillingConfig] = None


def configure_billing(config: BillingConfig) -> None:
    """
    Configure billing for jobs.
    
    Call this at app startup:
        from . import BillingConfig
        from .jobs import configure_billing
        
        configure_billing(BillingConfig.from_env())
    """
    global _billing_config
    _billing_config = config


def _get_config() -> BillingConfig:
    """Get billing config or raise if not configured."""
    if not _billing_config:
        raise RuntimeError("Billing not configured. Call configure_billing() at startup.")
    return _billing_config


def _get_services() -> tuple[BillingService, StripeSync]:
    """Get billing service and stripe sync instances."""
    config = _get_config()
    return BillingService(config), StripeSync(config)


def _get_db_connection():
    """Get database connection from kernel."""
    from ..app_kernel import get_db_connection
    return get_db_connection()


# =============================================================================
# Sync Jobs
# =============================================================================

async def sync_product_job(payload: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Sync a product to Stripe.
    
    Payload: {"product_id": "..."}
    """
    billing, sync = _get_services()
    
    async with _get_db_connection() as conn:
        product = await sync.sync_product(conn, billing, payload["product_id"])
    
    return {
        "status": "synced",
        "product_id": product["id"],
        "stripe_product_id": product.get("stripe_product_id"),
    }


async def sync_price_job(payload: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Sync a price to Stripe.
    
    Payload: {"price_id": "..."}
    """
    billing, sync = _get_services()
    
    async with _get_db_connection() as conn:
        price = await sync.sync_price(conn, billing, payload["price_id"])
    
    return {
        "status": "synced",
        "price_id": price["id"],
        "stripe_price_id": price.get("stripe_price_id"),
    }


async def sync_customer_job(payload: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Sync a customer to Stripe.
    
    Payload: {"customer_id": "..."}
    """
    billing, sync = _get_services()
    
    async with _get_db_connection() as conn:
        customer = await sync.sync_customer(conn, billing, payload["customer_id"])
    
    return {
        "status": "synced",
        "customer_id": customer["id"],
        "stripe_customer_id": customer.get("stripe_customer_id"),
    }


async def sync_subscription_job(payload: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Sync a subscription to Stripe.
    
    Payload: {
        "subscription_id": "...",
        "payment_method_id": "pm_..."  # Optional
    }
    """
    billing, sync = _get_services()
    
    async with _get_db_connection() as conn:
        subscription = await sync.sync_subscription(
            conn,
            billing,
            payload["subscription_id"],
            payment_method_id=payload.get("payment_method_id"),
        )
    
    return {
        "status": "synced",
        "subscription_id": subscription["id"],
        "stripe_subscription_id": subscription.get("stripe_subscription_id"),
    }


async def cancel_subscription_job(payload: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Cancel subscription in Stripe.
    
    Payload: {
        "subscription_id": "...",
        "immediately": false  # Optional
    }
    """
    billing, sync = _get_services()
    
    async with _get_db_connection() as conn:
        subscription = await sync.cancel_subscription_in_stripe(
            conn,
            billing,
            payload["subscription_id"],
            immediately=payload.get("immediately", False),
        )
    
    return {
        "status": "cancelled",
        "subscription_id": subscription["id"],
    }


# =============================================================================
# Reconciliation Jobs
# =============================================================================

async def reconcile_subscription_job(payload: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Reconcile a subscription with Stripe.
    
    Payload: {"subscription_id": "..."}
    """
    billing, sync = _get_services()
    
    async with _get_db_connection() as conn:
        subscription = await sync.reconcile_subscription(
            conn, billing, payload["subscription_id"]
        )
    
    return {
        "status": "reconciled",
        "subscription_id": subscription["id"],
        "subscription_status": subscription["status"],
    }


async def reconcile_all_subscriptions_job(payload: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Reconcile all subscriptions with Stripe.
    
    Payload: {} (empty)
    """
    billing, sync = _get_services()
    
    async with _get_db_connection() as conn:
        results = await sync.reconcile_all_subscriptions(conn, billing)
    
    reconciled = sum(1 for r in results if r["status"] == "reconciled")
    errors = sum(1 for r in results if r["status"] == "error")
    
    return {
        "status": "completed",
        "total": len(results),
        "reconciled": reconciled,
        "errors": errors,
    }


# =============================================================================
# Webhook Processing Jobs
# =============================================================================

async def process_webhook_job(payload: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Process a Stripe webhook event.
    
    Use this to process webhooks asynchronously for reliability.
    
    Payload: {
        "payload": webhook_body_string,
        "signature": stripe_signature_header,
    }
    """
    from .webhooks import WebhookHandler
    
    config = _get_config()
    billing = BillingService(config)
    handler = WebhookHandler(config)
    
    webhook_payload = payload["payload"]
    if isinstance(webhook_payload, str):
        webhook_payload = webhook_payload.encode()
    
    async with _get_db_connection() as conn:
        result = await handler.handle(
            conn,
            webhook_payload,
            payload["signature"],
            billing,
        )
    
    return result


# =============================================================================
# Scheduled Jobs
# =============================================================================

async def expire_trials_job(payload: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Check and expire ended trials.
    
    Run daily to catch any missed webhooks.
    Payload: {} (empty)
    """
    from datetime import datetime, timezone
    
    config = _get_config()
    billing = BillingService(config)
    
    async with _get_db_connection() as conn:
        now = datetime.now(timezone.utc).isoformat()
        
        subs = await billing.list_subscriptions(conn, status="trialing")
        expired = []
        
        for sub in subs:
            if sub.get("trial_end") and sub["trial_end"] < now:
                sub["status"] = "active"
                await conn.save_entity(BillingService.ENTITY_SUBSCRIPTION, sub)
                expired.append(sub["id"])
    
    return {
        "status": "completed",
        "expired_count": len(expired),
        "expired_ids": expired,
    }


async def cleanup_incomplete_subscriptions_job(payload: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Clean up old incomplete subscriptions.
    
    Run periodically to remove stale subscription attempts.
    Payload: {"max_age_days": 7}  # Optional, default 7
    """
    from datetime import datetime, timezone, timedelta
    
    config = _get_config()
    billing = BillingService(config)
    
    max_age_days = payload.get("max_age_days", 7)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    
    async with _get_db_connection() as conn:
        subs = await billing.list_subscriptions(conn, status="incomplete")
        cleaned = []
        
        for sub in subs:
            created = sub.get("created_at", "")
            if created and created < cutoff:
                sub["status"] = "cancelled"
                sub["cancelled_at"] = datetime.now(timezone.utc).isoformat()
                await conn.save_entity(BillingService.ENTITY_SUBSCRIPTION, sub)
                cleaned.append(sub["id"])
    
    return {
        "status": "completed",
        "cleaned_count": len(cleaned),
        "cleaned_ids": cleaned,
    }


# =============================================================================
# Task Registry - Register with kernel
# =============================================================================

BILLING_TASKS = {
    # Sync tasks
    "billing.sync_product": sync_product_job,
    "billing.sync_price": sync_price_job,
    "billing.sync_customer": sync_customer_job,
    "billing.sync_subscription": sync_subscription_job,
    "billing.cancel_subscription": cancel_subscription_job,
    
    # Reconciliation
    "billing.reconcile_subscription": reconcile_subscription_job,
    "billing.reconcile_all_subscriptions": reconcile_all_subscriptions_job,
    
    # Webhooks
    "billing.process_webhook": process_webhook_job,
    
    # Scheduled
    "billing.expire_trials": expire_trials_job,
    "billing.cleanup_incomplete": cleanup_incomplete_subscriptions_job,
}
