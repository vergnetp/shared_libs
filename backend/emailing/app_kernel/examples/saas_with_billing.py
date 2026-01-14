"""
Example: SaaS Service with Billing Auto-Wired

This shows how simple a SaaS service can be when using
the kernel's auto-wiring from manifest.yaml.

Just define your manifest and the kernel handles:
- Auth (login, register, password reset)
- SaaS (workspaces, members, invites)
- Billing (products, prices, subscriptions)
- Background jobs
"""

from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException

from .. import create_service, ServiceConfig, get_db_connection
from ..auth import require_auth

# =============================================================================
# Your App Routes
# =============================================================================

router = APIRouter(prefix="/widgets", tags=["widgets"])

@router.get("/")
async def list_widgets(user = Depends(require_auth)):
    """List user's widgets."""
    return {"widgets": []}

@router.post("/")
async def create_widget(name: str, user = Depends(require_auth)):
    """Create a widget (requires active subscription)."""
    # Feature gate example
    from ...billing import BillingService, BillingConfig
    
    billing_config = BillingConfig.from_env()
    billing = BillingService(billing_config)
    
    async with get_db_connection() as conn:
        # Check if user has pro plan
        if not await billing.user_has_feature(conn, user.id, "unlimited_widgets"):
            # Check widget count for free tier
            raise HTTPException(403, "Upgrade to Pro for unlimited widgets")
    
    return {"widget": {"id": "new-id", "name": name}}


# =============================================================================
# Database Schema
# =============================================================================

async def init_schema(db):
    """Initialize app tables."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS widgets (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            name TEXT NOT NULL,
            created_by TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)


# =============================================================================
# Create App
# =============================================================================

MANIFEST_PATH = str(Path(__file__).parent / "manifest.yaml")

app = create_service(
    name="my-saas",
    version="1.0.0",
    description="Example SaaS with billing auto-wired",
    
    # App routes only - auth, saas, billing handled by kernel
    routers=[router],
    
    # Config from manifest
    config=ServiceConfig.from_manifest(MANIFEST_PATH),
    
    # Enable auto-wiring (billing routes, tasks, etc.)
    manifest_path=MANIFEST_PATH,
    
    # App schema
    schema_init=init_schema,
)

# That's it! You now have:
# 
# Auth routes (auto):
#   POST /api/v1/auth/register
#   POST /api/v1/auth/login
#   POST /api/v1/auth/logout
#   GET  /api/v1/auth/me
#   POST /api/v1/auth/password/reset
#
# SaaS routes (auto when saas.enabled):
#   GET  /api/v1/workspaces
#   POST /api/v1/workspaces
#   GET  /api/v1/workspaces/{id}/members
#   POST /api/v1/workspaces/{id}/invite
#   ...
#
# Billing routes (auto when billing: in manifest):
#   GET  /api/v1/billing/plans
#   GET  /api/v1/billing/subscription
#   POST /api/v1/billing/subscribe
#   POST /api/v1/billing/portal
#   POST /api/v1/billing/cancel
#   GET  /api/v1/billing/invoices
#   POST /api/v1/billing/webhooks/stripe
#
# Plus your app routes:
#   GET  /api/v1/widgets
#   POST /api/v1/widgets


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
