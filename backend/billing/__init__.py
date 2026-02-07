"""
Billing - Your DB is the golden source.

A clean billing system that stores products, prices, and subscriptions
in your database while syncing to Stripe for payment processing.

Database connection is provided by app_kernel - no separate DB config needed.

Usage with app_kernel:
    from ..app_kernel import create_service, get_db_connection
    from ..app_kernel.auth import require_auth
    from . import (
        setup_billing_from_manifest,
        create_billing_router_from_manifest,
        BILLING_TASKS,
    )
    
    # Create billing router
    billing_router = create_billing_router_from_manifest(
        "manifest.yaml",
        get_db_connection,
        require_auth,
    )
    
    async def on_startup():
        await setup_billing_from_manifest("manifest.yaml", get_db_connection)
    
    app = create_service(
        name="my-service",
        routers=[billing_router],
        tasks=BILLING_TASKS,
        on_startup=on_startup,
        ...
    )

Example manifest.yaml:
    billing:
      stripe_secret_key: ${STRIPE_SECRET_KEY}
      stripe_publishable_key: ${STRIPE_PUBLISHABLE_KEY}
      stripe_webhook_secret: ${STRIPE_WEBHOOK_SECRET}
      trial_days: 14
      
      products:
        - slug: free
          name: Free Plan
          features: [basic_access]
          prices:
            - amount: 0
              interval: month
        
        - slug: pro
          name: Pro Plan
          features: [basic_access, api_access, priority_support]
          prices:
            - amount: 1999
              interval: month
            - amount: 19900
              interval: year

Billing Routes (auto-mounted):
    GET  /billing/plans              - List available plans
    GET  /billing/plans/{slug}       - Get specific plan
    GET  /billing/subscription       - Get current subscription
    POST /billing/subscribe          - Create checkout session
    POST /billing/portal             - Get customer portal URL
    POST /billing/cancel             - Cancel subscription
    POST /billing/reactivate         - Reactivate subscription
    GET  /billing/invoices           - List invoices
    GET  /billing/access/{feature}   - Check feature access
    POST /billing/webhooks/stripe    - Stripe webhook
"""

from .config import BillingConfig, StripeConfig
from .services import BillingService, SubscriptionStatus, PriceInterval
from .sync import StripeSync
from .webhooks import WebhookHandler
from .jobs import configure_billing, BILLING_TASKS
from .catalog import seed_catalog_from_manifest, setup_billing_from_manifest
from .router import create_billing_router, create_billing_router_from_manifest

__all__ = [
    # Config
    "BillingConfig",
    "StripeConfig",
    
    # Core service
    "BillingService",
    "SubscriptionStatus",
    "PriceInterval",
    
    # Stripe sync
    "StripeSync",
    
    # Webhooks
    "WebhookHandler",
    
    # Jobs (for kernel integration)
    "configure_billing",
    "BILLING_TASKS",
    
    # Catalog (manifest-driven setup)
    "seed_catalog_from_manifest",
    "setup_billing_from_manifest",
    
    # Router (standard billing endpoints)
    "create_billing_router",
    "create_billing_router_from_manifest",
]

__version__ = "0.1.0"
