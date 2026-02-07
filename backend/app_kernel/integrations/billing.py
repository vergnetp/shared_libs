"""
Billing Integration - Router and admin endpoints.

The billing module provides BillingService, StripeSync, BillingConfig.
This kernel integration creates the HTTP router and admin endpoints.

Usage:
    from app_kernel import create_service, BillingService

    async def seed_billing(db, billing: BillingService):
        pro = await billing.create_product(db, name="Pro", slug="pro", ...)
        await billing.create_price(db, product_id=pro["id"], amount_cents=1999, interval="month")

    app = create_service(
        name="my-api",
        stripe_secret_key="sk_live_...",
        seed_billing=seed_billing,
    )
"""

from typing import Optional, List, Dict, Any
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# =============================================================================
# Response Models
# =============================================================================

class PriceOut(BaseModel):
    id: str
    amount_cents: int
    currency: str
    interval: Optional[str] = None

class ProductOut(BaseModel):
    id: str
    name: str
    slug: str
    description: Optional[str] = None
    features: List[str] = []
    product_type: str = "subscription"
    prices: List[PriceOut] = []

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

class SubscribeRequest(BaseModel):
    price_id: str
    success_url: str
    cancel_url: str

class PurchaseRequest(BaseModel):
    price_id: str
    success_url: str
    cancel_url: str
    quantity: int = 1
    shipping_address: Optional[Dict[str, Any]] = None


# =============================================================================
# Router Factory
# =============================================================================

def create_billing_router(
    billing_service,
    stripe_sync,
    webhook_handler,
    billing_config,
    get_db_connection,
    require_auth,
    require_admin,
    *,
    prefix: str = "/billing",
    webhook_path: str = "/webhooks/stripe",
) -> APIRouter:
    """
    Create billing router with all endpoints including admin routes.
    
    This is called by kernel's create_service when stripe_secret_key is provided.
    """
    router = APIRouter(prefix=prefix, tags=["billing"])
    
    billing = billing_service
    sync = stripe_sync
    
    def _product_to_out(product: dict, prices: list) -> ProductOut:
        return ProductOut(
            id=product["id"],
            name=product["name"],
            slug=product["slug"],
            description=product.get("description"),
            features=product.get("features", []),
            product_type=product.get("product_type", "subscription"),
            prices=[
                PriceOut(
                    id=p["id"],
                    amount_cents=p["amount_cents"],
                    currency=p["currency"],
                    interval=p.get("interval"),
                )
                for p in prices
            ],
        )
    
    # -------------------------------------------------------------------------
    # Products (Public)
    # -------------------------------------------------------------------------
    
    @router.get("/products", response_model=List[ProductOut])
    async def list_products(active_only: bool = True):
        """List available products with prices."""
        async with get_db_connection() as conn:
            products = await billing.list_products(conn, active_only=active_only)
            result = []
            for product in products:
                prices = await billing.list_prices(conn, product_id=product["id"], active_only=active_only)
                result.append(_product_to_out(product, prices))
            return result
    
    @router.get("/products/{slug}", response_model=ProductOut)
    async def get_product(slug: str):
        """Get product by slug."""
        async with get_db_connection() as conn:
            product = await billing.get_product_by_slug(conn, slug)
            if not product:
                raise HTTPException(404, "Product not found")
            prices = await billing.list_prices(conn, product_id=product["id"])
            return _product_to_out(product, prices)
    
    # -------------------------------------------------------------------------
    # Subscription (Authenticated)
    # -------------------------------------------------------------------------
    
    @router.get("/subscription", response_model=Optional[SubscriptionOut])
    async def get_subscription(user=Depends(require_auth)):
        """Get current user's active subscription."""
        async with get_db_connection() as conn:
            customer = await billing.get_customer_by_user(conn, user.id)
            if not customer:
                return None
            
            sub = await billing.get_active_subscription(conn, customer["id"])
            if not sub:
                return None
            
            price = await billing.get_price(conn, sub["price_id"]) if sub.get("price_id") else None
            product = await billing.get_product(conn, price["product_id"]) if price else None
            prices = await billing.list_prices(conn, product_id=product["id"]) if product else []
            
            return SubscriptionOut(
                id=sub["id"],
                status=sub["status"],
                product=_product_to_out(product, prices) if product else None,
                price=PriceOut(
                    id=price["id"],
                    amount_cents=price["amount_cents"],
                    currency=price["currency"],
                    interval=price.get("interval"),
                ) if price else None,
                current_period_end=sub.get("current_period_end"),
                cancel_at_period_end=sub.get("cancel_at_period_end", False),
                trial_end=sub.get("trial_end"),
            )
    
    @router.post("/subscribe")
    async def create_subscription(req: SubscribeRequest, user=Depends(require_auth)):
        """Create Stripe checkout session for subscription."""
        async with get_db_connection() as conn:
            customer = await billing.get_or_create_customer(conn, user.id, user.email)
            customer = await sync.sync_customer(conn, billing, customer=customer)
            
            price = await billing.get_price(conn, req.price_id)
            if not price:
                raise HTTPException(404, "Price not found")
            
            product = await billing.get_product(conn, price["product_id"])
            if not product:
                raise HTTPException(404, "Product not found")
            
            price = await sync.sync_price(conn, billing, price=price, product=product)
            
            checkout_url = await sync.create_checkout_session(
                customer_id=customer["stripe_customer_id"],
                price_id=price["stripe_price_id"],
                success_url=req.success_url,
                cancel_url=req.cancel_url,
                mode="subscription",
            )
            
            return {"checkout_url": checkout_url}
    
    @router.post("/cancel")
    async def cancel_subscription(user=Depends(require_auth)):
        """Cancel current subscription at period end."""
        async with get_db_connection() as conn:
            customer = await billing.get_customer_by_user(conn, user.id)
            if not customer:
                raise HTTPException(404, "No subscription found")
            
            sub = await billing.get_active_subscription(conn, customer["id"])
            if not sub:
                raise HTTPException(404, "No active subscription")
            
            await sync.cancel_subscription(sub["stripe_subscription_id"])
            sub = await billing.update_subscription(conn, sub["id"], cancel_at_period_end=True)
            
            return {"status": "cancelled", "cancel_at_period_end": True}
    
    @router.post("/portal")
    async def customer_portal(user=Depends(require_auth)):
        """Get Stripe customer portal URL."""
        async with get_db_connection() as conn:
            customer = await billing.get_customer_by_user(conn, user.id)
            if not customer or not customer.get("stripe_customer_id"):
                raise HTTPException(404, "No billing account found")
            
            portal_url = await sync.create_portal_session(customer["stripe_customer_id"])
            return {"portal_url": portal_url}
    
    # -------------------------------------------------------------------------
    # One-time Purchases & Orders
    # -------------------------------------------------------------------------
    
    @router.post("/purchase")
    async def create_purchase(req: PurchaseRequest, user=Depends(require_auth)):
        """Create checkout for one-time or physical purchase."""
        async with get_db_connection() as conn:
            customer = await billing.get_or_create_customer(conn, user.id, user.email)
            customer = await sync.sync_customer(conn, billing, customer=customer)
            
            price = await billing.get_price(conn, req.price_id)
            if not price:
                raise HTTPException(404, "Price not found")
            
            product = await billing.get_product(conn, price["product_id"])
            if not product:
                raise HTTPException(404, "Product not found")
            
            price = await sync.sync_price(conn, billing, price=price, product=product)
            
            checkout_url = await sync.create_checkout_session(
                customer_id=customer["stripe_customer_id"],
                price_id=price["stripe_price_id"],
                success_url=req.success_url,
                cancel_url=req.cancel_url,
                mode="payment",
                quantity=req.quantity,
            )
            
            return {"checkout_url": checkout_url}
    
    @router.get("/orders", response_model=List[OrderOut])
    async def list_orders(status: Optional[str] = None, user=Depends(require_auth)):
        """List user's orders."""
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
    
    # -------------------------------------------------------------------------
    # Feature Access
    # -------------------------------------------------------------------------
    
    @router.get("/access/{feature}")
    async def check_feature_access(feature: str, user=Depends(require_auth)):
        """Check if user has access to a feature."""
        async with get_db_connection() as conn:
            has_access = await billing.user_has_feature(conn, user.id, feature)
            return {"feature": feature, "has_access": has_access}
    
    @router.get("/purchased/{product_slug}")
    async def check_purchased(product_slug: str, user=Depends(require_auth)):
        """Check if user has purchased a product."""
        async with get_db_connection() as conn:
            has_purchased = await billing.user_has_purchased(conn, user.id, product_slug)
            return {"product": product_slug, "purchased": has_purchased}
    
    # -------------------------------------------------------------------------
    # Admin Routes
    # -------------------------------------------------------------------------
    
    @router.get("/admin/subscriptions", tags=["billing-admin"])
    async def admin_list_subscriptions(
        status: Optional[str] = None,
        limit: int = Query(50, le=200),
        admin=Depends(require_admin)
    ):
        """[Admin] List all subscriptions."""
        async with get_db_connection() as conn:
            from billing import BillingService
            filters = {"status": status} if status else {}
            return await conn.find_entities(
                BillingService.ENTITY_SUBSCRIPTION,
                filters=filters,
                limit=limit
            )
    
    @router.get("/admin/orders", tags=["billing-admin"])
    async def admin_list_orders(
        status: Optional[str] = None,
        limit: int = Query(50, le=200),
        admin=Depends(require_admin)
    ):
        """[Admin] List all orders."""
        async with get_db_connection() as conn:
            from billing import BillingService
            filters = {"status": status} if status else {}
            return await conn.find_entities(
                BillingService.ENTITY_ORDER,
                filters=filters,
                limit=limit
            )
    
    @router.get("/admin/customers", tags=["billing-admin"])
    async def admin_list_customers(
        limit: int = Query(50, le=200),
        admin=Depends(require_admin)
    ):
        """[Admin] List all customers."""
        async with get_db_connection() as conn:
            from billing import BillingService
            return await conn.find_entities(
                BillingService.ENTITY_CUSTOMER,
                limit=limit
            )
    
    @router.get("/admin/customers/{customer_id}", tags=["billing-admin"])
    async def admin_get_customer(customer_id: str, admin=Depends(require_admin)):
        """[Admin] Get customer with subscriptions and orders."""
        async with get_db_connection() as conn:
            customer = await billing.get_customer(conn, customer_id)
            if not customer:
                raise HTTPException(404, "Customer not found")
            subs = await billing.list_subscriptions(conn, customer_id=customer_id)
            orders = await billing.list_orders(conn, customer_id=customer_id)
            return {"customer": customer, "subscriptions": subs, "orders": orders}
    
    @router.get("/admin/revenue", tags=["billing-admin"])
    async def admin_revenue_stats(admin=Depends(require_admin)):
        """[Admin] Revenue statistics."""
        async with get_db_connection() as conn:
            from billing import BillingService
            active_subs = await conn.find_entities(
                BillingService.ENTITY_SUBSCRIPTION,
                filters={"status": "active"}
            )
            paid_orders = await conn.find_entities(
                BillingService.ENTITY_ORDER,
                filters={"status": "paid"}
            )
            
            mrr = 0
            for sub in active_subs:
                if sub.get("price_id"):
                    price = await billing.get_price(conn, sub["price_id"])
                    if price:
                        if price.get("interval") == "month":
                            mrr += price.get("amount_cents", 0)
                        elif price.get("interval") == "year":
                            mrr += price.get("amount_cents", 0) // 12
            
            return {
                "active_subscriptions": len(active_subs),
                "total_orders": len(paid_orders),
                "mrr_cents": mrr,
            }
    
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
