"""
BillingService - Core billing operations.

Your DB is the golden source. Stripe is the payment processor.
All reads come from your DB, writes sync to Stripe.
"""

import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from enum import Enum

from ..config import BillingConfig


class SubscriptionStatus(str, Enum):
    """Subscription status values."""
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"
    TRIALING = "trialing"
    INCOMPLETE = "incomplete"
    UNPAID = "unpaid"
    PAUSED = "paused"


class PriceInterval(str, Enum):
    """Price billing interval."""
    MONTH = "month"
    YEAR = "year"
    WEEK = "week"
    DAY = "day"
    ONE_TIME = None  # For one-time purchases


class BillingService:
    """
    Core billing service - manages products, prices, customers, and subscriptions.
    
    Your DB is truth. Stripe IDs are stored but all business logic 
    queries your DB, not Stripe.
    
    Connection is injected - you control the lifecycle.
    
    Usage:
        from databases import DatabaseManager
        
        config = BillingConfig(...)
        billing = BillingService(config)
        
        async with DatabaseManager.connect("postgres", **db_config) as conn:
            # Create a product
            product = await billing.create_product(conn,
                name="Pro Plan",
                slug="pro",
                description="Full access to all features"
            )
            
            # Add a price
            price = await billing.create_price(conn,
                product_id=product["id"],
                amount_cents=1999,
                interval="month"
            )
            
            # All in same transaction if needed
            async with conn.transaction():
                await billing.create_subscription(conn,
                    customer_id=customer["id"],
                    price_id=price["id"]
                )
    """
    
    # Entity names
    ENTITY_PRODUCT = "billing_product"
    ENTITY_PRICE = "billing_price"
    ENTITY_CUSTOMER = "billing_customer"
    ENTITY_SUBSCRIPTION = "billing_subscription"
    ENTITY_INVOICE = "billing_invoice"
    ENTITY_PAYMENT_METHOD = "billing_payment_method"
    ENTITY_USAGE = "billing_usage"
    ENTITY_ORDER = "billing_order"  # For one-time and physical purchases
    
    def __init__(self, config: BillingConfig):
        """
        Initialize billing service.
        
        Args:
            config: BillingConfig instance
        """
        self.config = config
    
    # ──────────────────────────────────────────────────────────────────
    # Products
    # ──────────────────────────────────────────────────────────────────
    
    async def create_product(
        self,
        conn,
        name: str,
        slug: str,
        description: str = None,
        features: List[str] = None,
        metadata: Dict[str, Any] = None,
        active: bool = True,
        product_type: str = "subscription",  # subscription, one_time, physical
        shippable: bool = False,
    ) -> Dict[str, Any]:
        """
        Create a new product.
        
        Args:
            conn: Database connection
            name: Display name
            slug: URL-friendly identifier (unique) - e.g., "pro-plan", "widget-pack", "tshirt-xl"
            description: Product description
            features: List of feature flags (for subscription access control)
            metadata: Additional data (images, specs, etc.)
            active: Whether product is available
            product_type: Type of product:
                - "subscription": Recurring billing (SaaS plans)
                - "one_time": One-time digital purchase (credits, lifetime access)
                - "physical": Physical goods (requires shipping)
            shippable: Whether product requires shipping address
            
        Returns:
            Created product entity
        """
        product = {
            "id": str(uuid.uuid4()),
            "name": name,
            "slug": slug,
            "description": description,
            "features": features or [],
            "metadata": metadata or {},
            "active": active,
            "product_type": product_type,
            "shippable": shippable,
            "stripe_product_id": None,
        }
        
        return await conn.save_entity(self.ENTITY_PRODUCT, product)
    
    async def get_product(self, conn, product_id: str) -> Optional[Dict[str, Any]]:
        """Get product by ID."""
        return await conn.get_entity(self.ENTITY_PRODUCT, product_id)
    
    async def get_product_by_slug(self, conn, slug: str) -> Optional[Dict[str, Any]]:
        """Get product by slug."""
        results = await conn.find_entities(
            self.ENTITY_PRODUCT,
            filters={"slug": slug},
            limit=1
        )
        return results[0] if results else None
    
    async def list_products(self, conn, active_only: bool = True) -> List[Dict[str, Any]]:
        """List all products."""
        filters = {"active": True} if active_only else {}
        try:
            return await conn.find_entities(self.ENTITY_PRODUCT, filters=filters)
        except Exception:
            # Table may not exist yet on first run
            return []
    
    async def update_product(
        self,
        conn,
        product_id: str,
        **updates
    ) -> Dict[str, Any]:
        """Update product fields."""
        product = await self.get_product(conn, product_id)
        if not product:
            raise ValueError(f"Product {product_id} not found")
        
        product.update(updates)
        return await conn.save_entity(self.ENTITY_PRODUCT, product)
    
    async def deactivate_product(self, conn, product_id: str) -> Dict[str, Any]:
        """Deactivate a product (don't delete - archive)."""
        return await self.update_product(conn, product_id, active=False)
    
    # ──────────────────────────────────────────────────────────────────
    # Prices
    # ──────────────────────────────────────────────────────────────────
    
    async def create_price(
        self,
        conn,
        product_id: str,
        amount_cents: int,
        currency: str = None,
        interval: str = None,
        interval_count: int = 1,
        nickname: str = None,
        metadata: Dict[str, Any] = None,
        active: bool = True,
        skip_product_validation: bool = False,
    ) -> Dict[str, Any]:
        """
        Create a new price for a product.
        
        Prices are immutable in Stripe - to change, create new and deactivate old.
        
        Args:
            conn: Database connection
            product_id: Parent product ID
            amount_cents: Price in cents (1999 = $19.99)
            currency: ISO currency code (defaults to config)
            interval: Billing interval or None for one-time
            interval_count: Number of intervals between billings
            nickname: Internal name for the price
            metadata: Additional data
            active: Whether price is available
            skip_product_validation: Skip product existence check (use when caller already verified)
            
        Returns:
            Created price entity
        """
        if not skip_product_validation:
            product = await self.get_product(conn, product_id)
            if not product:
                raise ValueError(f"Product {product_id} not found")
        
        price = {
            "id": str(uuid.uuid4()),
            "product_id": product_id,
            "amount_cents": amount_cents,
            "currency": currency or self.config.default_currency,
            "interval": interval,
            "interval_count": interval_count,
            "nickname": nickname,
            "metadata": metadata or {},
            "active": active,
            "stripe_price_id": None,
        }
        
        return await conn.save_entity(self.ENTITY_PRICE, price)
    
    async def get_price(self, conn, price_id: str) -> Optional[Dict[str, Any]]:
        """Get price by ID."""
        return await conn.get_entity(self.ENTITY_PRICE, price_id)
    
    async def list_prices(
        self,
        conn,
        product_id: str = None,
        active_only: bool = True
    ) -> List[Dict[str, Any]]:
        """List prices, optionally filtered by product."""
        filters = {}
        if product_id:
            filters["product_id"] = product_id
        if active_only:
            filters["active"] = True
        try:
            return await conn.find_entities(self.ENTITY_PRICE, filters=filters)
        except Exception:
            # Table may not exist yet on first run
            return []
    
    async def deactivate_price(self, conn, price_id: str) -> Dict[str, Any]:
        """Deactivate a price (prices are immutable, so deactivate instead of edit)."""
        price = await self.get_price(conn, price_id)
        if not price:
            raise ValueError(f"Price {price_id} not found")
        price["active"] = False
        return await conn.save_entity(self.ENTITY_PRICE, price)
    
    # ──────────────────────────────────────────────────────────────────
    # Customers
    # ──────────────────────────────────────────────────────────────────
    
    async def create_customer(
        self,
        conn,
        user_id: str,
        email: str,
        name: str = None,
        metadata: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        Create a billing customer linked to your user.
        
        Args:
            conn: Database connection
            user_id: Your internal user ID
            email: Customer email
            name: Customer name
            metadata: Additional data
            
        Returns:
            Created customer entity
        """
        existing = await self.get_customer_by_user(conn, user_id)
        if existing:
            return existing
        
        customer = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "email": email,
            "name": name,
            "metadata": metadata or {},
            "stripe_customer_id": None,
        }
        
        return await conn.save_entity(self.ENTITY_CUSTOMER, customer)
    
    async def get_customer(self, conn, customer_id: str) -> Optional[Dict[str, Any]]:
        """Get customer by ID."""
        return await conn.get_entity(self.ENTITY_CUSTOMER, customer_id)
    
    async def get_customer_by_user(self, conn, user_id: str) -> Optional[Dict[str, Any]]:
        """Get customer by your internal user ID."""
        results = await conn.find_entities(
            self.ENTITY_CUSTOMER,
            filters={"user_id": user_id},
            limit=1
        )
        return results[0] if results else None
    
    async def get_or_create_customer(
        self,
        conn,
        user_id: str,
        email: str,
        name: str = None,
        metadata: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        Get existing customer or create new one.
        
        Convenience method - same as create_customer but name is clearer.
        """
        return await self.create_customer(conn, user_id, email, name, metadata)
    
    async def update_customer(
        self,
        conn,
        customer_id: str,
        **updates
    ) -> Dict[str, Any]:
        """Update customer fields."""
        customer = await self.get_customer(conn, customer_id)
        if not customer:
            raise ValueError(f"Customer {customer_id} not found")
        customer.update(updates)
        return await conn.save_entity(self.ENTITY_CUSTOMER, customer)
    
    # ──────────────────────────────────────────────────────────────────
    # Subscriptions
    # ──────────────────────────────────────────────────────────────────
    
    async def create_subscription(
        self,
        conn,
        customer_id: str,
        price_id: str,
        trial_days: int = None,
        metadata: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        Create a subscription.
        
        Args:
            conn: Database connection
            customer_id: Billing customer ID
            price_id: Price ID to subscribe to
            trial_days: Trial period (defaults to config)
            metadata: Additional data
            
        Returns:
            Created subscription entity
        """
        customer = await self.get_customer(conn, customer_id)
        if not customer:
            raise ValueError(f"Customer {customer_id} not found")
        
        price = await self.get_price(conn, price_id)
        if not price:
            raise ValueError(f"Price {price_id} not found")
        
        trial = trial_days if trial_days is not None else self.config.trial_days
        now = datetime.now(timezone.utc)
        
        period_end = self._calculate_period_end(now, price)
        
        status = SubscriptionStatus.TRIALING if trial > 0 else SubscriptionStatus.ACTIVE
        
        subscription = {
            "id": str(uuid.uuid4()),
            "customer_id": customer_id,
            "price_id": price_id,
            "status": status.value,
            "current_period_start": now.isoformat(),
            "current_period_end": period_end.isoformat(),
            "trial_end": self._calculate_trial_end(now, trial) if trial > 0 else None,
            "cancel_at_period_end": False,
            "cancelled_at": None,
            "metadata": metadata or {},
            "stripe_subscription_id": None,
        }
        
        return await conn.save_entity(self.ENTITY_SUBSCRIPTION, subscription)
    
    async def get_subscription(self, conn, subscription_id: str) -> Optional[Dict[str, Any]]:
        """Get subscription by ID."""
        return await conn.get_entity(self.ENTITY_SUBSCRIPTION, subscription_id)
    
    async def get_active_subscription(self, conn, customer_id: str) -> Optional[Dict[str, Any]]:
        """Get customer's active subscription (most recent if multiple)."""
        results = await conn.find_entities(
            self.ENTITY_SUBSCRIPTION,
            filters={
                "customer_id": customer_id,
                "status": SubscriptionStatus.ACTIVE.value,
            },
            order_by="created_at DESC",
            limit=1
        )
        return results[0] if results else None
    
    async def list_subscriptions(
        self,
        conn,
        customer_id: str = None,
        status: str = None,
    ) -> List[Dict[str, Any]]:
        """List subscriptions with optional filters."""
        filters = {}
        if customer_id:
            filters["customer_id"] = customer_id
        if status:
            filters["status"] = status
        return await conn.find_entities(self.ENTITY_SUBSCRIPTION, filters=filters)
    
    async def cancel_subscription(
        self,
        conn,
        subscription_id: str,
        at_period_end: bool = True,
    ) -> Dict[str, Any]:
        """
        Cancel a subscription.
        
        Args:
            conn: Database connection
            subscription_id: Subscription to cancel
            at_period_end: If True, cancel at end of current period.
        """
        sub = await self.get_subscription(conn, subscription_id)
        if not sub:
            raise ValueError(f"Subscription {subscription_id} not found")
        
        now = datetime.now(timezone.utc)
        
        if at_period_end:
            sub["cancel_at_period_end"] = True
            comment = "Subscription cancelled (at period end)"
        else:
            sub["status"] = SubscriptionStatus.CANCELLED.value
            sub["cancelled_at"] = now.isoformat()
            comment = "Subscription cancelled (immediately)"
        
        return await conn.save_entity(self.ENTITY_SUBSCRIPTION, sub, comment=comment)
    
    async def reactivate_subscription(self, conn, subscription_id: str) -> Dict[str, Any]:
        """Reactivate a subscription that was set to cancel at period end."""
        sub = await self.get_subscription(conn, subscription_id)
        if not sub:
            raise ValueError(f"Subscription {subscription_id} not found")
        
        if sub["status"] == SubscriptionStatus.CANCELLED.value:
            raise ValueError("Cannot reactivate a fully cancelled subscription")
        
        sub["cancel_at_period_end"] = False
        return await conn.save_entity(
            self.ENTITY_SUBSCRIPTION, 
            sub, 
            comment="Subscription reactivated"
        )
    
    async def change_subscription_price(
        self,
        conn,
        subscription_id: str,
        new_price_id: str,
        prorate: bool = True,
    ) -> Dict[str, Any]:
        """
        Change subscription to a different price (upgrade/downgrade).
        """
        sub = await self.get_subscription(conn, subscription_id)
        if not sub:
            raise ValueError(f"Subscription {subscription_id} not found")
        
        price = await self.get_price(conn, new_price_id)
        if not price:
            raise ValueError(f"Price {new_price_id} not found")
        
        sub["price_id"] = new_price_id
        sub["prorate_on_next_sync"] = prorate
        
        return await conn.save_entity(self.ENTITY_SUBSCRIPTION, sub)
    
    # ──────────────────────────────────────────────────────────────────
    # Invoices
    # ──────────────────────────────────────────────────────────────────
    
    async def get_invoice(self, conn, invoice_id: str) -> Optional[Dict[str, Any]]:
        """Get invoice by ID."""
        return await conn.get_entity(self.ENTITY_INVOICE, invoice_id)
    
    async def list_invoices(
        self,
        conn,
        customer_id: str = None,
        subscription_id: str = None,
        status: str = None,
    ) -> List[Dict[str, Any]]:
        """List invoices with optional filters."""
        filters = {}
        if customer_id:
            filters["customer_id"] = customer_id
        if subscription_id:
            filters["subscription_id"] = subscription_id
        if status:
            filters["status"] = status
        return await conn.find_entities(self.ENTITY_INVOICE, filters=filters)
    
    # ──────────────────────────────────────────────────────────────────
    # Access Control Helpers
    # ──────────────────────────────────────────────────────────────────
    
    async def user_has_active_subscription(self, conn, user_id: str) -> bool:
        """Check if user has an active subscription."""
        customer = await self.get_customer_by_user(conn, user_id)
        if not customer:
            return False
        
        sub = await self.get_active_subscription(conn, customer["id"])
        return sub is not None
    
    async def user_has_feature(self, conn, user_id: str, feature: str) -> bool:
        """Check if user has access to a specific feature."""
        customer = await self.get_customer_by_user(conn, user_id)
        if not customer:
            return False
        
        sub = await self.get_active_subscription(conn, customer["id"])
        if not sub:
            return False
        
        price = await self.get_price(conn, sub["price_id"])
        if not price:
            return False
        
        product = await self.get_product(conn, price["product_id"])
        if not product:
            return False
        
        return feature in (product.get("features") or [])
    
    async def get_user_plan(self, conn, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get user's current plan details.
        
        Returns dict with product, price, and subscription info.
        """
        customer = await self.get_customer_by_user(conn, user_id)
        if not customer:
            return None
        
        sub = await self.get_active_subscription(conn, customer["id"])
        if not sub:
            return None
        
        price = await self.get_price(conn, sub["price_id"])
        product = await self.get_product(conn, price["product_id"]) if price else None
        
        return {
            "customer": customer,
            "subscription": sub,
            "price": price,
            "product": product,
        }
    
    # ──────────────────────────────────────────────────────────────────
    # Orders (One-time and Physical Purchases)
    # ──────────────────────────────────────────────────────────────────
    
    async def create_order(
        self,
        conn,
        customer_id: str,
        price_id: str,
        quantity: int = 1,
        shipping_address: Dict[str, Any] = None,
        metadata: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        Create an order for one-time or physical purchase.
        
        Args:
            customer_id: Your customer ID
            price_id: Your price ID
            quantity: Number of items
            shipping_address: Shipping address for physical products
            metadata: Additional order data
        """
        customer = await self.get_customer(conn, customer_id)
        if not customer:
            raise ValueError(f"Customer {customer_id} not found")
        
        price = await self.get_price(conn, price_id)
        if not price:
            raise ValueError(f"Price {price_id} not found")
        
        product = await self.get_product(conn, price["product_id"])
        
        order = {
            "id": str(uuid.uuid4()),
            "customer_id": customer_id,
            "price_id": price_id,
            "product_id": price["product_id"],
            "quantity": quantity,
            "amount_cents": price["amount_cents"] * quantity,
            "currency": price["currency"],
            "status": "pending",  # pending, paid, shipped, delivered, cancelled, refunded
            "product_type": product.get("product_type", "one_time"),
            "shipping_address": shipping_address,
            "tracking_number": None,
            "shipped_at": None,
            "delivered_at": None,
            "stripe_payment_intent_id": None,
            "stripe_checkout_session_id": None,
            "metadata": metadata or {},
        }
        
        return await conn.save_entity(self.ENTITY_ORDER, order)
    
    async def get_order(self, conn, order_id: str) -> Optional[Dict[str, Any]]:
        """Get order by ID."""
        return await conn.get_entity(self.ENTITY_ORDER, order_id)
    
    async def list_orders(
        self,
        conn,
        customer_id: str = None,
        status: str = None,
    ) -> List[Dict[str, Any]]:
        """List orders with optional filters."""
        filters = {}
        if customer_id:
            filters["customer_id"] = customer_id
        if status:
            filters["status"] = status
        return await conn.find_entities(self.ENTITY_ORDER, filters=filters)
    
    async def update_order_status(
        self,
        conn,
        order_id: str,
        status: str,
        tracking_number: str = None,
    ) -> Dict[str, Any]:
        """Update order status (for fulfillment)."""
        order = await self.get_order(conn, order_id)
        if not order:
            raise ValueError(f"Order {order_id} not found")
        
        now = datetime.now(timezone.utc).isoformat()
        order["status"] = status
        
        if tracking_number:
            order["tracking_number"] = tracking_number
        
        if status == "shipped":
            order["shipped_at"] = now
        elif status == "delivered":
            order["delivered_at"] = now
        
        return await conn.save_entity(self.ENTITY_ORDER, order)
    
    async def user_has_purchased(
        self,
        conn,
        user_id: str,
        product_slug: str,
    ) -> bool:
        """Check if user has purchased a product (one-time purchase check)."""
        customer = await self.get_customer_by_user(conn, user_id)
        if not customer:
            return False
        
        product = await self.get_product_by_slug(conn, product_slug)
        if not product:
            return False
        
        orders = await conn.find_entities(
            self.ENTITY_ORDER,
            filters={
                "customer_id": customer["id"],
                "product_id": product["id"],
                "status": "paid",
            },
            limit=1
        )
        return len(orders) > 0
    
    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────
    
    def _calculate_period_end(self, start: datetime, price: Dict[str, Any]) -> datetime:
        """Calculate subscription period end based on price interval."""
        from dateutil.relativedelta import relativedelta
        
        interval = price.get("interval")
        count = price.get("interval_count", 1)
        
        if interval == "month":
            return start + relativedelta(months=count)
        elif interval == "year":
            return start + relativedelta(years=count)
        elif interval == "week":
            return start + relativedelta(weeks=count)
        elif interval == "day":
            return start + relativedelta(days=count)
        else:
            return start
    
    def _calculate_trial_end(self, start: datetime, trial_days: int) -> str:
        """Calculate trial end date."""
        from dateutil.relativedelta import relativedelta
        return (start + relativedelta(days=trial_days)).isoformat()
