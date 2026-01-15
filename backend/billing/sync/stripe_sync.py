"""
StripeSync - Syncs your DB entities to Stripe.

Your DB is truth. This service pushes changes to Stripe
and stores the returned Stripe IDs.

Uses cloud.AsyncStripeClient for:
- Proper async (non-blocking)
- Automatic retries with exponential backoff
- Circuit breaker for resilience
"""

import uuid
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

from ..config import BillingConfig
from ..services.billing_service import BillingService


class StripeSync:
    """
    Handles synchronization between your DB and Stripe.
    
    Pattern:
    1. Create entity in your DB (via BillingService)
    2. Call sync method to push to Stripe
    3. Stripe ID stored back in your DB
    
    Connection is injected - you control the lifecycle.
    
    Usage:
        from databases import DatabaseManager
        
        billing = BillingService(config)
        sync = StripeSync(config)
        
        async with DatabaseManager.connect("postgres", **db_config) as conn:
            # Create locally
            product = await billing.create_product(conn, name="Pro", slug="pro")
            
            # Push to Stripe
            product = await sync.sync_product(conn, billing, product["id"])
            # product now has stripe_product_id set
    """
    
    def __init__(self, config: BillingConfig):
        self.config = config
        self._client = None  # Lazy init
    
    def _get_client(self):
        """Get or create AsyncStripeClient."""
        if self._client is None:
            from ...cloud import AsyncStripeClient
            self._client = AsyncStripeClient(
                api_key=self.config.stripe.secret_key,
                api_version=self.config.stripe.api_version,
            )
        return self._client
    
    async def close(self):
        """Close the Stripe client."""
        if self._client:
            await self._client.close()
            self._client = None
    
    # ──────────────────────────────────────────────────────────────────
    # Product Sync
    # ──────────────────────────────────────────────────────────────────
    
    async def sync_product(
        self,
        conn,
        billing: BillingService,
        product_id: str = None,
        product: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        Sync a product to Stripe.
        
        Creates in Stripe if no stripe_product_id, updates if exists.
        
        Args:
            conn: Database connection
            billing: BillingService instance
            product_id: ID of product to fetch and sync
            product: Product dict directly (avoids re-fetching)
        """
        if product is None:
            if product_id is None:
                raise ValueError("Either product_id or product must be provided")
            product = await billing.get_product(conn, product_id)
            if not product:
                raise ValueError(f"Product {product_id} not found")
        
        client = self._get_client()
        
        metadata = {
            "local_id": product["id"],
            "slug": product["slug"],
            **(product.get("metadata") or {}),
        }
        
        if product.get("stripe_product_id"):
            # Update existing
            await client.modify_product(
                product_id=product["stripe_product_id"],
                name=product["name"],
                description=product.get("description") or "",
                active=product.get("active", True),
                metadata=metadata,
            )
        else:
            # Create new
            stripe_product = await client.create_product(
                name=product["name"],
                description=product.get("description") or "",
                active=product.get("active", True),
                metadata=metadata,
            )
            product["stripe_product_id"] = stripe_product["id"]
            await conn.save_entity(BillingService.ENTITY_PRODUCT, product)
        
        return product
    
    # ──────────────────────────────────────────────────────────────────
    # Price Sync
    # ──────────────────────────────────────────────────────────────────
    
    async def sync_price(
        self,
        conn,
        billing: BillingService,
        price_id: str = None,
        price: Dict[str, Any] = None,
        product: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        Sync a price to Stripe.
        
        Prices in Stripe are immutable - can only create or deactivate.
        
        Args:
            conn: Database connection
            billing: BillingService instance
            price_id: ID of price to fetch and sync
            price: Price dict directly (avoids re-fetching)
            product: Product dict directly (avoids re-fetching)
        """
        if price is None:
            if price_id is None:
                raise ValueError("Either price_id or price must be provided")
            price = await billing.get_price(conn, price_id)
            if not price:
                raise ValueError(f"Price {price_id} not found")
        
        # Ensure product is available
        if product is None:
            product = await billing.get_product(conn, price["product_id"])
            if not product:
                raise ValueError(f"Product {price['product_id']} not found")
        
        if not product.get("stripe_product_id"):
            product = await self.sync_product(conn, billing, product=product)
        
        client = self._get_client()
        
        if price.get("stripe_price_id"):
            # Prices are immutable - only update active status
            await client.modify_price(
                price_id=price["stripe_price_id"],
                active=price.get("active", True),
            )
        else:
            # Create new price
            metadata = {
                "local_id": price["id"],
                **(price.get("metadata") or {}),
            }
            
            # Build recurring dict if not one-time
            recurring = None
            if price.get("interval"):
                recurring = {
                    "interval": price["interval"],
                    "interval_count": price.get("interval_count", 1),
                }
            
            stripe_price = await client.create_price(
                product_id=product["stripe_product_id"],
                unit_amount=price["amount_cents"],
                currency=price["currency"],
                recurring=recurring,
                nickname=price.get("nickname"),
                active=price.get("active", True),
                metadata=metadata,
            )
            price["stripe_price_id"] = stripe_price["id"]
            await conn.save_entity(BillingService.ENTITY_PRICE, price)
        
        return price
    
    # ──────────────────────────────────────────────────────────────────
    # Customer Sync
    # ──────────────────────────────────────────────────────────────────
    
    async def sync_customer(
        self,
        conn,
        billing: BillingService,
        customer_id: str,
    ) -> Dict[str, Any]:
        """
        Sync a customer to Stripe.
        """
        customer = await billing.get_customer(conn, customer_id)
        if not customer:
            raise ValueError(f"Customer {customer_id} not found")
        
        client = self._get_client()
        
        metadata = {
            "local_id": customer["id"],
            "user_id": customer["user_id"],
            **(customer.get("metadata") or {}),
        }
        
        if customer.get("stripe_customer_id"):
            # Update existing
            await client.modify_customer(
                customer_id=customer["stripe_customer_id"],
                email=customer["email"],
                name=customer.get("name"),
                metadata=metadata,
            )
        else:
            # Create new
            stripe_customer = await client.create_customer(
                email=customer["email"],
                name=customer.get("name"),
                metadata=metadata,
            )
            customer["stripe_customer_id"] = stripe_customer["id"]
            await conn.save_entity(BillingService.ENTITY_CUSTOMER, customer)
        
        return customer
    
    # ──────────────────────────────────────────────────────────────────
    # Subscription Sync
    # ──────────────────────────────────────────────────────────────────
    
    async def sync_subscription(
        self,
        conn,
        billing: BillingService,
        subscription_id: str,
    ) -> Dict[str, Any]:
        """
        Sync a subscription to Stripe.
        
        If subscription has stripe_subscription_id, updates Stripe.
        Otherwise creates in Stripe.
        """
        sub = await billing.get_subscription(conn, subscription_id)
        if not sub:
            raise ValueError(f"Subscription {subscription_id} not found")
        
        # Ensure customer is synced
        customer = await billing.get_customer(conn, sub["customer_id"])
        if not customer:
            raise ValueError(f"Customer {sub['customer_id']} not found")
        
        if not customer.get("stripe_customer_id"):
            customer = await self.sync_customer(conn, billing, customer["id"])
        
        # Ensure price is synced
        price = await billing.get_price(conn, sub["price_id"])
        if not price:
            raise ValueError(f"Price {sub['price_id']} not found")
        
        if not price.get("stripe_price_id"):
            price = await self.sync_price(conn, billing, price=price)
        
        client = self._get_client()
        
        if sub.get("stripe_subscription_id"):
            # Update existing - fetch current state first
            stripe_sub = await client.retrieve_subscription(sub["stripe_subscription_id"])
            
            # Handle cancellation
            if sub.get("status") == "cancelled" and stripe_sub.get("status") != "canceled":
                await client.modify_subscription(
                    subscription_id=sub["stripe_subscription_id"],
                    cancel_at_period_end=True,
                )
        else:
            # Create new subscription in Stripe
            stripe_sub = await client.create_subscription(
                customer_id=customer["stripe_customer_id"],
                items=[{"price": price["stripe_price_id"]}],
                metadata={
                    "local_id": sub["id"],
                    **(sub.get("metadata") or {}),
                },
            )
            
            # Update local with Stripe data
            sub["stripe_subscription_id"] = stripe_sub["id"]
            sub["status"] = stripe_sub["status"]
            
            if stripe_sub.get("current_period_start"):
                sub["current_period_start"] = datetime.fromtimestamp(
                    stripe_sub["current_period_start"], tz=timezone.utc
                ).isoformat()
            if stripe_sub.get("current_period_end"):
                sub["current_period_end"] = datetime.fromtimestamp(
                    stripe_sub["current_period_end"], tz=timezone.utc
                ).isoformat()
            
            await conn.save_entity(BillingService.ENTITY_SUBSCRIPTION, sub)
        
        return sub
    
    async def cancel_subscription_in_stripe(
        self,
        conn,
        billing: BillingService,
        subscription_id: str,
        immediately: bool = False,
    ) -> Dict[str, Any]:
        """
        Cancel subscription in Stripe.
        
        Args:
            immediately: If True, cancel now. If False, cancel at period end.
        """
        sub = await billing.get_subscription(conn, subscription_id)
        if not sub:
            raise ValueError(f"Subscription {subscription_id} not found")
        
        if not sub.get("stripe_subscription_id"):
            return sub  # Not synced, nothing to do
        
        client = self._get_client()
        
        if immediately:
            stripe_sub = await client.cancel_subscription(
                subscription_id=sub["stripe_subscription_id"],
                invoice_now=False,
                prorate=True,
            )
        else:
            stripe_sub = await client.modify_subscription(
                subscription_id=sub["stripe_subscription_id"],
                cancel_at_period_end=True,
            )
        
        # Update local state
        sub["status"] = stripe_sub.get("status", sub["status"])
        sub["cancel_at_period_end"] = stripe_sub.get("cancel_at_period_end", False)
        
        if stripe_sub.get("canceled_at"):
            sub["cancelled_at"] = datetime.fromtimestamp(
                stripe_sub["canceled_at"], tz=timezone.utc
            ).isoformat()
        
        await conn.save_entity(BillingService.ENTITY_SUBSCRIPTION, sub)
        return sub
    
    async def update_subscription_price(
        self,
        conn,
        billing: BillingService,
        subscription_id: str,
        new_price_id: str,
        proration_behavior: str = "create_prorations",
    ) -> Dict[str, Any]:
        """
        Change subscription to a different price (upgrade/downgrade).
        
        Args:
            proration_behavior: 'create_prorations', 'none', or 'always_invoice'
        """
        sub = await billing.get_subscription(conn, subscription_id)
        if not sub:
            raise ValueError(f"Subscription {subscription_id} not found")
        
        if not sub.get("stripe_subscription_id"):
            raise ValueError(f"Subscription {subscription_id} not synced to Stripe")
        
        # Ensure new price is synced
        new_price = await billing.get_price(conn, new_price_id)
        if not new_price:
            raise ValueError(f"Price {new_price_id} not found")
        
        if not new_price.get("stripe_price_id"):
            new_price = await self.sync_price(conn, billing, price=new_price)
        
        client = self._get_client()
        
        # Get current subscription to find item ID
        stripe_sub = await client.retrieve_subscription(sub["stripe_subscription_id"])
        
        if not stripe_sub.get("items", {}).get("data"):
            raise ValueError("No subscription items found")
        
        item_id = stripe_sub["items"]["data"][0]["id"]
        
        # Update subscription with new price
        updated_sub = await client.modify_subscription(
            subscription_id=sub["stripe_subscription_id"],
            items=[{
                "id": item_id,
                "price": new_price["stripe_price_id"],
            }],
            proration_behavior=proration_behavior,
        )
        
        # Update local
        sub["price_id"] = new_price_id
        sub["status"] = updated_sub.get("status", sub["status"])
        
        if updated_sub.get("current_period_end"):
            sub["current_period_end"] = datetime.fromtimestamp(
                updated_sub["current_period_end"], tz=timezone.utc
            ).isoformat()
        
        await conn.save_entity(BillingService.ENTITY_SUBSCRIPTION, sub)
        return sub
    
    # ──────────────────────────────────────────────────────────────────
    # Payment Method
    # ──────────────────────────────────────────────────────────────────
    
    async def attach_payment_method(
        self,
        conn,
        billing: BillingService,
        customer_id: str,
        payment_method_id: str,
        set_as_default: bool = True,
    ) -> Dict[str, Any]:
        """
        Attach a payment method to a customer.
        
        Args:
            payment_method_id: Stripe PaymentMethod ID (from frontend)
            set_as_default: If True, set as default payment method
        """
        customer = await billing.get_customer(conn, customer_id)
        if not customer:
            raise ValueError(f"Customer {customer_id} not found")
        
        if not customer.get("stripe_customer_id"):
            customer = await self.sync_customer(conn, billing, customer["id"])
        
        client = self._get_client()
        
        # Attach payment method to customer
        await client.attach_payment_method(
            payment_method_id=payment_method_id,
            customer_id=customer["stripe_customer_id"],
        )
        
        # Set as default if requested
        if set_as_default:
            await client.modify_customer(
                customer_id=customer["stripe_customer_id"],
                invoice_settings={"default_payment_method": payment_method_id},
            )
        
        return customer
    
    # ──────────────────────────────────────────────────────────────────
    # Checkout Sessions (Stripe-hosted payment page)
    # ──────────────────────────────────────────────────────────────────
    
    async def create_checkout_session(
        self,
        conn,
        billing: BillingService,
        customer_id: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
        mode: str = "subscription",
        quantity: int = 1,
        trial_days: int = None,
        allow_promotion_codes: bool = False,
        metadata: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        """
        Create a Stripe Checkout session.
        
        Returns session with 'url' to redirect user to.
        
        Args:
            mode: 'subscription', 'payment', or 'setup'
            trial_days: Free trial period (subscription mode only)
        """
        # Ensure customer is synced
        customer = await billing.get_customer(conn, customer_id)
        if not customer:
            raise ValueError(f"Customer {customer_id} not found")
        
        if not customer.get("stripe_customer_id"):
            customer = await self.sync_customer(conn, billing, customer["id"])
        
        # Ensure price is synced
        price = await billing.get_price(conn, price_id)
        if not price:
            raise ValueError(f"Price {price_id} not found")
        
        if not price.get("stripe_price_id"):
            price = await self.sync_price(conn, billing, price=price)
        
        client = self._get_client()
        
        # Build checkout params
        checkout_metadata = {
            "local_customer_id": customer_id,
            "local_price_id": price_id,
            "product_type": mode,
            **(metadata or {}),
        }
        
        # Subscription-specific params
        subscription_data = None
        if mode == "subscription" and trial_days:
            subscription_data = {"trial_period_days": trial_days}
        
        session = await client.create_checkout_session(
            customer_id=customer["stripe_customer_id"],
            line_items=[{
                "price": price["stripe_price_id"],
                "quantity": quantity,
            }],
            mode=mode,
            success_url=success_url,
            cancel_url=cancel_url,
            allow_promotion_codes=allow_promotion_codes,
            subscription_data=subscription_data,
            metadata=checkout_metadata,
        )
        
        return session
    
    async def get_checkout_session(self, session_id: str) -> Dict[str, Any]:
        """Retrieve a checkout session by ID."""
        client = self._get_client()
        return await client.retrieve_checkout_session(session_id)
    
    # ──────────────────────────────────────────────────────────────────
    # Customer Portal
    # ──────────────────────────────────────────────────────────────────
    
    async def create_portal_session(
        self,
        conn,
        billing: BillingService,
        customer_id: str,
        return_url: str,
    ) -> str:
        """
        Create a Stripe Customer Portal session.
        
        Returns the portal URL for redirect.
        """
        customer = await billing.get_customer(conn, customer_id)
        if not customer:
            raise ValueError(f"Customer {customer_id} not found")
        
        if not customer.get("stripe_customer_id"):
            raise ValueError(f"Customer {customer_id} not synced to Stripe")
        
        client = self._get_client()
        
        session = await client.create_portal_session(
            customer_id=customer["stripe_customer_id"],
            return_url=return_url,
        )
        
        return session["url"]
    
    # ──────────────────────────────────────────────────────────────────
    # Reconciliation
    # ──────────────────────────────────────────────────────────────────
    
    async def reconcile_subscription(
        self,
        conn,
        billing: BillingService,
        subscription_id: str,
    ) -> Dict[str, Any]:
        """
        Reconcile local subscription with Stripe state.
        
        Fetches current state from Stripe and updates your DB.
        Use for periodic checks or when webhooks might have been missed.
        """
        sub = await billing.get_subscription(conn, subscription_id)
        if not sub:
            raise ValueError(f"Subscription {subscription_id} not found")
        
        if not sub.get("stripe_subscription_id"):
            return sub  # Not synced yet
        
        client = self._get_client()
        stripe_sub = await client.retrieve_subscription(sub["stripe_subscription_id"])
        
        # Update local state from Stripe
        sub["status"] = stripe_sub["status"]
        sub["cancel_at_period_end"] = stripe_sub.get("cancel_at_period_end", False)
        
        if stripe_sub.get("current_period_start"):
            sub["current_period_start"] = datetime.fromtimestamp(
                stripe_sub["current_period_start"], tz=timezone.utc
            ).isoformat()
        if stripe_sub.get("current_period_end"):
            sub["current_period_end"] = datetime.fromtimestamp(
                stripe_sub["current_period_end"], tz=timezone.utc
            ).isoformat()
        if stripe_sub.get("canceled_at"):
            sub["cancelled_at"] = datetime.fromtimestamp(
                stripe_sub["canceled_at"], tz=timezone.utc
            ).isoformat()
        
        await conn.save_entity(BillingService.ENTITY_SUBSCRIPTION, sub)
        return sub
    
    async def reconcile_all_subscriptions(
        self,
        conn,
        billing: BillingService,
    ) -> List[Dict[str, Any]]:
        """Reconcile all synced subscriptions."""
        subs = await billing.list_subscriptions(conn)
        results = []
        
        for sub in subs:
            if sub.get("stripe_subscription_id"):
                try:
                    updated = await self.reconcile_subscription(conn, billing, sub["id"])
                    results.append({"id": sub["id"], "status": "reconciled", "data": updated})
                except Exception as e:
                    results.append({"id": sub["id"], "status": "error", "error": str(e)})
        
        return results
    
    # ──────────────────────────────────────────────────────────────────
    # Checkout Session Processing (No Webhook Required)
    # ──────────────────────────────────────────────────────────────────
    
    async def process_checkout_session(
        self,
        conn,
        billing: BillingService,
        session_id: str,
    ) -> Dict[str, Any]:
        """
        Process a completed checkout session.
        
        Call this after user is redirected back from Stripe Checkout.
        Creates subscription/order in your DB without requiring webhooks.
        
        Args:
            conn: Database connection
            billing: BillingService instance
            session_id: Stripe Checkout Session ID (from ?session_id= param)
            
        Returns:
            Dict with created subscription/order info
        """
        client = self._get_client()
        
        # Fetch session from Stripe
        session = await client.retrieve_checkout_session(session_id)
        
        # Verify payment completed
        if session.get("payment_status") != "paid" and session.get("status") != "complete":
            return {
                "status": "pending",
                "payment_status": session.get("payment_status"),
                "session_status": session.get("status"),
            }
        
        metadata = session.get("metadata", {})
        local_customer_id = metadata.get("local_customer_id")
        local_price_id = metadata.get("local_price_id")
        product_type = metadata.get("product_type", "subscription")
        
        if not local_customer_id or not local_price_id:
            return {"status": "error", "error": "Missing metadata in session"}
        
        # Handle subscription mode
        if session.get("mode") == "subscription" and session.get("subscription"):
            stripe_sub_id = session["subscription"]
            
            # Check if we already have this subscription
            existing = await conn.find_entities(
                BillingService.ENTITY_SUBSCRIPTION,
                filters={"stripe_subscription_id": stripe_sub_id},
                limit=1
            )
            
            if existing:
                # Already processed (maybe via webhook)
                return {
                    "status": "exists",
                    "subscription_id": existing[0]["id"],
                }
            
            # Fetch full subscription details from Stripe
            stripe_sub = await client.retrieve_subscription(stripe_sub_id)
            
            # Create local subscription
            sub = {
                "id": str(uuid.uuid4()),
                "customer_id": local_customer_id,
                "price_id": local_price_id,
                "status": stripe_sub["status"],
                "stripe_subscription_id": stripe_sub_id,
                "cancel_at_period_end": stripe_sub.get("cancel_at_period_end", False),
            }
            
            if stripe_sub.get("current_period_start"):
                sub["current_period_start"] = datetime.fromtimestamp(
                    stripe_sub["current_period_start"], tz=timezone.utc
                ).isoformat()
            if stripe_sub.get("current_period_end"):
                sub["current_period_end"] = datetime.fromtimestamp(
                    stripe_sub["current_period_end"], tz=timezone.utc
                ).isoformat()
            
            await conn.save_entity(BillingService.ENTITY_SUBSCRIPTION, sub)
            
            return {
                "status": "created",
                "type": "subscription",
                "subscription_id": sub["id"],
            }
        
        # Handle one-time payment mode
        elif session.get("mode") == "payment":
            # Check if we already have this order
            existing = await conn.find_entities(
                BillingService.ENTITY_ORDER,
                filters={"stripe_session_id": session_id},
                limit=1
            )
            
            if existing:
                return {
                    "status": "exists",
                    "order_id": existing[0]["id"],
                }
            
            # Get price for amount
            price = await billing.get_price(conn, local_price_id)
            
            # Create order
            order = {
                "id": str(uuid.uuid4()),
                "customer_id": local_customer_id,
                "price_id": local_price_id,
                "status": "paid",
                "amount_cents": price["amount_cents"] if price else session.get("amount_total"),
                "currency": price["currency"] if price else session.get("currency"),
                "stripe_session_id": session_id,
                "stripe_payment_intent_id": session.get("payment_intent"),
                "product_type": product_type,
            }
            
            # Add shipping if collected
            if session.get("shipping_details"):
                order["shipping_name"] = session["shipping_details"].get("name")
                order["shipping_address"] = session["shipping_details"].get("address", {})
            
            await conn.save_entity(BillingService.ENTITY_ORDER, order)
            
            return {
                "status": "created",
                "type": "order",
                "order_id": order["id"],
            }
        
        return {"status": "unknown_mode", "mode": session.get("mode")}
