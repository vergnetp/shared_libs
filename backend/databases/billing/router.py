"""
Billing Routes - REST endpoints for billing.

Auto-mounted by kernel when billing is enabled in manifest.

Endpoints:
    # Products (plans, one-time, physical)
    GET  /billing/products           - List all products
    GET  /billing/products/{slug}    - Get specific product
    
    # Subscriptions
    GET  /billing/subscription       - Current user's subscription
    POST /billing/subscribe          - Create subscription checkout
    GET  /billing/checkout/verify    - Verify checkout (no webhook needed)
    POST /billing/cancel             - Cancel subscription
    POST /billing/reactivate         - Reactivate subscription
    
    # Purchases (one-time, physical)
    POST /billing/purchase           - Create purchase checkout
    GET  /billing/orders             - List user's orders
    GET  /billing/orders/{id}        - Get order details
    
    # General
    POST /billing/portal             - Stripe customer portal URL
    GET  /billing/invoices           - List invoices
    GET  /billing/access/{feature}   - Check feature access
    POST /billing/webhooks/stripe    - Webhook handler (optional backup)
"""

from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel

from .config import BillingConfig
from .services import BillingService
from .sync import StripeSync
from .webhooks import WebhookHandler


# =============================================================================
# Schemas
# =============================================================================

class PriceOut(BaseModel):
    id: str
    amount_cents: int
    currency: str
    interval: Optional[str] = None
    interval_count: int = 1
    nickname: Optional[str] = None


class ProductOut(BaseModel):
    id: str
    slug: str
    name: str
    description: Optional[str] = None
    product_type: str = "subscription"
    features: List[str] = []
    shippable: bool = False
    prices: List[PriceOut] = []
    metadata: Dict[str, Any] = {}


class SubscriptionOut(BaseModel):
    id: str
    status: str
    product: Optional[ProductOut] = None
    price: Optional[PriceOut] = None
    current_period_end: Optional[str] = None
    cancel_at_period_end: bool = False
    trial_end: Optional[str] = None


class OrderOut(BaseModel):
    id: str
    status: str
    product: Optional[ProductOut] = None
    price: Optional[PriceOut] = None
    quantity: int = 1
    amount_cents: int
    currency: str
    shipping_address: Optional[Dict[str, Any]] = None
    tracking_number: Optional[str] = None
    created_at: Optional[str] = None


class CheckoutRequest(BaseModel):
    price_id: str
    success_url: str
    cancel_url: str
    quantity: int = 1


class PortalRequest(BaseModel):
    return_url: str


class CancelRequest(BaseModel):
    immediately: bool = False


class InvoiceOut(BaseModel):
    id: str
    status: Optional[str] = None
    amount_due: Optional[int] = None
    amount_paid: Optional[int] = None
    currency: Optional[str] = None
    invoice_pdf: Optional[str] = None
    hosted_invoice_url: Optional[str] = None


# =============================================================================
# Router Factory
# =============================================================================

def create_billing_router(
    billing_config: BillingConfig,
    get_db_connection,
    require_auth,
    *,
    prefix: str = "/billing",
    webhook_path: str = "/webhooks/stripe",
) -> APIRouter:
    """Create billing router with injected dependencies."""
    
    router = APIRouter(prefix=prefix, tags=["billing"])
    
    billing = BillingService(billing_config)
    sync = StripeSync(billing_config)
    webhook_handler = WebhookHandler(billing_config)
    
    def _product_to_out(product: dict, prices: list) -> ProductOut:
        """Convert product dict to ProductOut."""
        return ProductOut(
            id=product["id"],
            slug=product["slug"],
            name=product["name"],
            description=product.get("description"),
            product_type=product.get("product_type", "subscription"),
            features=product.get("features", []),
            shippable=product.get("shippable", False),
            metadata=product.get("metadata", {}),
            prices=[PriceOut(
                id=p["id"],
                amount_cents=p["amount_cents"],
                currency=p["currency"],
                interval=p.get("interval"),
                interval_count=p.get("interval_count", 1),
                nickname=p.get("nickname"),
            ) for p in prices],
        )
    
    # -------------------------------------------------------------------------
    # Products (Public)
    # -------------------------------------------------------------------------
    
    @router.get("/products", response_model=List[ProductOut])
    async def list_products(product_type: Optional[str] = None):
        """
        List all available products.
        
        Filter by product_type: subscription, one_time, physical
        """
        async with get_db_connection() as conn:
            products = await billing.list_products(conn, active_only=True)
            
            if product_type:
                products = [p for p in products if p.get("product_type") == product_type]
            
            result = []
            for product in products:
                prices = await billing.list_prices(conn, product_id=product["id"], active_only=True)
                result.append(_product_to_out(product, prices))
            
            return result
    
    @router.get("/products/{slug}", response_model=ProductOut)
    async def get_product(slug: str):
        """Get a specific product by slug."""
        async with get_db_connection() as conn:
            product = await billing.get_product_by_slug(conn, slug)
            if not product:
                raise HTTPException(404, "Product not found")
            
            prices = await billing.list_prices(conn, product_id=product["id"], active_only=True)
            return _product_to_out(product, prices)
    
    # Backward compatibility
    @router.get("/plans", response_model=List[ProductOut], include_in_schema=False)
    async def list_plans():
        return await list_products(product_type="subscription")
    
    # -------------------------------------------------------------------------
    # Subscription (Authenticated)
    # -------------------------------------------------------------------------
    
    @router.get("/subscription", response_model=Optional[SubscriptionOut])
    async def get_subscription(user = Depends(require_auth)):
        """Get current user's subscription."""
        async with get_db_connection() as conn:
            customer = await billing.get_customer_by_user(conn, user.id)
            if not customer:
                return None
            
            subs = await billing.list_subscriptions(conn, customer_id=customer["id"])
            active_sub = next(
                (s for s in subs if s["status"] in ("active", "trialing", "past_due")),
                None
            )
            
            if not active_sub:
                return None
            
            price = await billing.get_price(conn, active_sub["price_id"])
            product = await billing.get_product(conn, price["product_id"]) if price else None
            prices = await billing.list_prices(conn, product_id=product["id"]) if product else []
            
            return SubscriptionOut(
                id=active_sub["id"],
                status=active_sub["status"],
                product=_product_to_out(product, prices) if product else None,
                price=PriceOut(
                    id=price["id"],
                    amount_cents=price["amount_cents"],
                    currency=price["currency"],
                    interval=price.get("interval"),
                    interval_count=price.get("interval_count", 1),
                    nickname=price.get("nickname"),
                ) if price else None,
                current_period_end=active_sub.get("current_period_end"),
                cancel_at_period_end=active_sub.get("cancel_at_period_end", False),
                trial_end=active_sub.get("trial_end"),
            )
    
    @router.post("/subscribe")
    async def subscribe(req: CheckoutRequest, user = Depends(require_auth)):
        """
        Subscribe to a plan or change existing plan.
        
        If user has no active subscription: Creates Stripe Checkout session.
        If user has active subscription: Changes plan immediately with proration.
        """
        async with get_db_connection() as conn:
            customer = await billing.get_or_create_customer(
                conn, user_id=user.id, email=user.email,
                name=getattr(user, "name", None),
            )
            
            price = await billing.get_price(conn, req.price_id)
            if not price:
                raise HTTPException(400, "Invalid price_id")
            
            # Check for existing active subscription
            active_sub = await billing.get_active_subscription(conn, customer["id"])
            
            if active_sub and active_sub.get("stripe_subscription_id"):
                # Already subscribed - change plan instead of new checkout
                if active_sub.get("price_id") == req.price_id:
                    # Same plan - just reactivate if cancelled
                    if active_sub.get("cancel_at_period_end"):
                        sub = await billing.reactivate_subscription(conn, active_sub["id"])
                        return {
                            "action": "reactivated",
                            "subscription_id": sub["id"],
                            "message": "Subscription reactivated",
                        }
                    else:
                        return {
                            "action": "no_change",
                            "subscription_id": active_sub["id"],
                            "message": "Already subscribed to this plan",
                        }
                
                # Different plan - change it
                updated_sub = await sync.change_subscription_plan(
                    conn, billing, active_sub["id"], req.price_id
                )
                
                return {
                    "action": "plan_changed",
                    "subscription_id": updated_sub["id"],
                    "message": "Plan changed successfully. Proration applied.",
                }
            
            # No active subscription - create checkout
            checkout_url = await sync.create_checkout_session(
                conn, billing, customer["id"], req.price_id,
                success_url=req.success_url,
                cancel_url=req.cancel_url,
            )
            
            return {"checkout_url": checkout_url}
    
    @router.get("/checkout/verify")
    async def verify_checkout(session_id: str = Query(...), user = Depends(require_auth)):
        """
        Verify checkout session after Stripe redirect.
        
        Call this when user returns from Stripe Checkout to create the
        local subscription/order. Works without webhooks.
        
        Add `?session_id={CHECKOUT_SESSION_ID}` to your success_url and
        Stripe will replace it with the actual session ID.
        
        Example success_url:
            https://yourapp.com/success?session_id={CHECKOUT_SESSION_ID}
        """
        async with get_db_connection() as conn:
            # Verify the session belongs to this user's customer
            customer = await billing.get_customer_by_user(conn, user.id)
            if not customer:
                raise HTTPException(400, "No billing account found")
            
            result = await sync.verify_checkout_session(conn, billing, session_id)
            
            return result
    
    @router.post("/cancel")
    async def cancel_subscription(req: CancelRequest = CancelRequest(), user = Depends(require_auth)):
        """Cancel current subscription."""
        async with get_db_connection() as conn:
            customer = await billing.get_customer_by_user(conn, user.id)
            if not customer:
                raise HTTPException(400, "No billing account found")
            
            sub = await billing.get_active_subscription(conn, customer["id"])
            if not sub:
                raise HTTPException(400, "No active subscription")
            
            sub = await billing.cancel_subscription(conn, sub["id"], at_period_end=not req.immediately)
            
            if sub.get("stripe_subscription_id"):
                await sync.cancel_subscription_in_stripe(conn, billing, sub["id"], immediately=req.immediately)
            
            return {"status": "cancelled" if req.immediately else "scheduled", "subscription_id": sub["id"]}
    
    @router.post("/reactivate")
    async def reactivate_subscription(user = Depends(require_auth)):
        """Reactivate a cancelled subscription."""
        async with get_db_connection() as conn:
            customer = await billing.get_customer_by_user(conn, user.id)
            if not customer:
                raise HTTPException(400, "No billing account found")
            
            subs = await billing.list_subscriptions(conn, customer_id=customer["id"])
            sub = next((s for s in subs if s.get("cancel_at_period_end") and s["status"] == "active"), None)
            
            if not sub:
                raise HTTPException(400, "No subscription to reactivate")
            
            sub = await billing.reactivate_subscription(conn, sub["id"])
            
            return {"status": "reactivated", "subscription_id": sub["id"]}
    
    # -------------------------------------------------------------------------
    # Purchase / Orders (One-time and Physical)
    # -------------------------------------------------------------------------
    
    @router.post("/purchase")
    async def purchase(req: CheckoutRequest, user = Depends(require_auth)):
        """
        Create purchase checkout (one-time or physical product).
        
        Automatically detects mode from price/product type.
        """
        async with get_db_connection() as conn:
            customer = await billing.get_or_create_customer(
                conn, user_id=user.id, email=user.email,
                name=getattr(user, "name", None),
            )
            
            price = await billing.get_price(conn, req.price_id)
            if not price:
                raise HTTPException(400, "Invalid price_id")
            
            # Create checkout (auto-detects mode and shipping)
            checkout_url = await sync.create_checkout_session(
                conn, billing, customer["id"], req.price_id,
                success_url=req.success_url,
                cancel_url=req.cancel_url,
                quantity=req.quantity,
            )
            
            return {"checkout_url": checkout_url}
    
    @router.get("/orders", response_model=List[OrderOut])
    async def list_orders(status: Optional[str] = None, user = Depends(require_auth)):
        """List user's orders (one-time and physical purchases)."""
        async with get_db_connection() as conn:
            customer = await billing.get_customer_by_user(conn, user.id)
            if not customer:
                return []
            
            orders = await billing.list_orders(conn, customer_id=customer["id"], status=status)
            
            result = []
            for order in orders:
                price = await billing.get_price(conn, order["price_id"])
                product = await billing.get_product(conn, order["product_id"])
                prices = await billing.list_prices(conn, product_id=product["id"]) if product else []
                
                result.append(OrderOut(
                    id=order["id"],
                    status=order["status"],
                    product=_product_to_out(product, prices) if product else None,
                    price=PriceOut(
                        id=price["id"],
                        amount_cents=price["amount_cents"],
                        currency=price["currency"],
                        interval=price.get("interval"),
                    ) if price else None,
                    quantity=order.get("quantity", 1),
                    amount_cents=order["amount_cents"],
                    currency=order["currency"],
                    shipping_address=order.get("shipping_address"),
                    tracking_number=order.get("tracking_number"),
                    created_at=order.get("created_at"),
                ))
            
            return result
    
    @router.get("/orders/{order_id}", response_model=OrderOut)
    async def get_order(order_id: str, user = Depends(require_auth)):
        """Get order details."""
        async with get_db_connection() as conn:
            customer = await billing.get_customer_by_user(conn, user.id)
            if not customer:
                raise HTTPException(404, "Order not found")
            
            order = await billing.get_order(conn, order_id)
            if not order or order["customer_id"] != customer["id"]:
                raise HTTPException(404, "Order not found")
            
            price = await billing.get_price(conn, order["price_id"])
            product = await billing.get_product(conn, order["product_id"])
            prices = await billing.list_prices(conn, product_id=product["id"]) if product else []
            
            return OrderOut(
                id=order["id"],
                status=order["status"],
                product=_product_to_out(product, prices) if product else None,
                price=PriceOut(
                    id=price["id"],
                    amount_cents=price["amount_cents"],
                    currency=price["currency"],
                    interval=price.get("interval"),
                ) if price else None,
                quantity=order.get("quantity", 1),
                amount_cents=order["amount_cents"],
                currency=order["currency"],
                shipping_address=order.get("shipping_address"),
                tracking_number=order.get("tracking_number"),
                created_at=order.get("created_at"),
            )
    
    # -------------------------------------------------------------------------
    # General
    # -------------------------------------------------------------------------
    
    @router.post("/portal")
    async def customer_portal(req: PortalRequest, user = Depends(require_auth)):
        """Get Stripe Customer Portal URL."""
        async with get_db_connection() as conn:
            customer = await billing.get_customer_by_user(conn, user.id)
            if not customer:
                raise HTTPException(400, "No billing account found")
            
            if not customer.get("stripe_customer_id"):
                raise HTTPException(400, "Billing account not synced")
            
            portal_url = await sync.create_portal_session(
                conn, billing, customer["id"], return_url=req.return_url,
            )
            
            return {"portal_url": portal_url}
    
    @router.get("/invoices", response_model=List[InvoiceOut])
    async def list_invoices(limit: int = Query(10, le=100), user = Depends(require_auth)):
        """List user's invoices."""
        async with get_db_connection() as conn:
            customer = await billing.get_customer_by_user(conn, user.id)
            if not customer:
                return []
            
            invoices = await billing.list_invoices(conn, customer_id=customer["id"])
            
            return [InvoiceOut(
                id=inv["id"],
                status=inv.get("status"),
                amount_due=inv.get("amount_due"),
                amount_paid=inv.get("amount_paid"),
                currency=inv.get("currency"),
                invoice_pdf=inv.get("invoice_pdf"),
                hosted_invoice_url=inv.get("hosted_invoice_url"),
            ) for inv in invoices[:limit]]
    
    @router.get("/access/{feature}")
    async def check_feature_access(feature: str, user = Depends(require_auth)):
        """Check if user has access to a feature."""
        async with get_db_connection() as conn:
            has_access = await billing.user_has_feature(conn, user.id, feature)
            return {"feature": feature, "has_access": has_access}
    
    @router.get("/purchased/{product_slug}")
    async def check_purchased(product_slug: str, user = Depends(require_auth)):
        """Check if user has purchased a product (one-time purchase)."""
        async with get_db_connection() as conn:
            has_purchased = await billing.user_has_purchased(conn, user.id, product_slug)
            return {"product": product_slug, "purchased": has_purchased}
    
    # -------------------------------------------------------------------------
    # Webhook
    # -------------------------------------------------------------------------
    
    @router.post(webhook_path, include_in_schema=False)
    async def stripe_webhook(request: Request):
        """Handle Stripe webhook events."""
        payload = await request.body()
        signature = request.headers.get("stripe-signature")
        
        if not signature:
            raise HTTPException(400, "Missing stripe-signature header")
        
        async with get_db_connection() as conn:
            result = await webhook_handler.handle(conn, payload, signature, billing)
        
        if result.get("status") == "error" and "Invalid signature" in result.get("error", ""):
            raise HTTPException(400, "Invalid signature")
        
        return {"received": True}
    
    return router


def create_billing_router_from_manifest(
    manifest_path: str,
    get_db_connection,
    require_auth,
) -> APIRouter:
    """Create billing router from manifest.yaml."""
    from .catalog import _load_manifest
    
    manifest = _load_manifest(manifest_path)
    billing_section = manifest.get("billing", {})
    config = BillingConfig.from_manifest(billing_section)
    
    return create_billing_router(config, get_db_connection, require_auth)
