"""
StripeSync - Syncs your DB entities to Stripe.

Your DB is truth. This service pushes changes to Stripe
and stores the returned Stripe IDs.
"""

import uuid
import stripe
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
        stripe.api_key = config.stripe.secret_key
        stripe.api_version = config.stripe.api_version
    
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
        
        stripe_data = {
            "name": product["name"],
            "description": product.get("description") or "",
            "active": product.get("active", True),
            "metadata": {
                "local_id": product["id"],
                "slug": product["slug"],
                **(product.get("metadata") or {}),
            },
        }
        
        if product.get("stripe_product_id"):
            # Update existing
            stripe_product = stripe.Product.modify(
                product["stripe_product_id"],
                **stripe_data
            )
        else:
            # Create new
            stripe_product = stripe.Product.create(**stripe_data)
            product["stripe_product_id"] = stripe_product.id
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
        
        if price.get("stripe_price_id"):
            # Prices are immutable - only update active status
            stripe_price = stripe.Price.modify(
                price["stripe_price_id"],
                active=price.get("active", True),
            )
        else:
            # Create new price
            stripe_data = {
                "product": product["stripe_product_id"],
                "currency": price["currency"],
                "unit_amount": price["amount_cents"],
                "active": price.get("active", True),
                "metadata": {
                    "local_id": price["id"],
                    **(price.get("metadata") or {}),
                },
            }
            
            # Add recurring if not one-time
            if price.get("interval"):
                stripe_data["recurring"] = {
                    "interval": price["interval"],
                    "interval_count": price.get("interval_count", 1),
                }
            
            if price.get("nickname"):
                stripe_data["nickname"] = price["nickname"]
            
            stripe_price = stripe.Price.create(**stripe_data)
            price["stripe_price_id"] = stripe_price.id
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
        
        stripe_data = {
            "email": customer["email"],
            "name": customer.get("name"),
            "metadata": {
                "local_id": customer["id"],
                "user_id": customer["user_id"],
                **(customer.get("metadata") or {}),
            },
        }
        
        if customer.get("stripe_customer_id"):
            # Update existing
            stripe_customer = stripe.Customer.modify(
                customer["stripe_customer_id"],
                **stripe_data
            )
        else:
            # Create new
            stripe_customer = stripe.Customer.create(**stripe_data)
            customer["stripe_customer_id"] = stripe_customer.id
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
        payment_method_id: str = None,
    ) -> Dict[str, Any]:
        """
        Sync a subscription to Stripe.
        
        For new subscriptions, requires a payment method.
        """
        sub = await billing.get_subscription(conn, subscription_id)
        if not sub:
            raise ValueError(f"Subscription {subscription_id} not found")
        
        # Ensure customer is synced
        customer = await billing.get_customer(conn, sub["customer_id"])
        if not customer.get("stripe_customer_id"):
            customer = await self.sync_customer(conn, billing, customer["id"])
        
        # Ensure price is synced
        price = await billing.get_price(conn, sub["price_id"])
        if not price.get("stripe_price_id"):
            price = await self.sync_price(conn, billing, price["id"])
        
        if sub.get("stripe_subscription_id"):
            # Update existing subscription
            update_data = {}
            
            if sub.get("cancel_at_period_end"):
                update_data["cancel_at_period_end"] = True
            
            if sub.get("prorate_on_next_sync"):
                # Handle plan change
                stripe_sub = stripe.Subscription.retrieve(sub["stripe_subscription_id"])
                update_data["items"] = [{
                    "id": stripe_sub["items"]["data"][0]["id"],
                    "price": price["stripe_price_id"],
                }]
                update_data["proration_behavior"] = "create_prorations"
                sub.pop("prorate_on_next_sync", None)
            
            if update_data:
                stripe.Subscription.modify(
                    sub["stripe_subscription_id"],
                    **update_data
                )
        else:
            # Create new subscription
            stripe_data = {
                "customer": customer["stripe_customer_id"],
                "items": [{"price": price["stripe_price_id"]}],
                "metadata": {
                    "local_id": sub["id"],
                    **(sub.get("metadata") or {}),
                },
            }
            
            # Add trial if specified
            if sub.get("trial_end"):
                trial_end = datetime.fromisoformat(sub["trial_end"].replace("Z", "+00:00"))
                stripe_data["trial_end"] = int(trial_end.timestamp())
            
            # Set payment method if provided
            if payment_method_id:
                stripe_data["default_payment_method"] = payment_method_id
            
            stripe_sub = stripe.Subscription.create(**stripe_data)
            sub["stripe_subscription_id"] = stripe_sub.id
            await conn.save_entity(BillingService.ENTITY_SUBSCRIPTION, sub)
        
        return sub
    
    async def cancel_subscription_in_stripe(
        self,
        conn,
        billing: BillingService,
        subscription_id: str,
        immediately: bool = False,
    ) -> Dict[str, Any]:
        """Cancel subscription in Stripe."""
        sub = await billing.get_subscription(conn, subscription_id)
        if not sub:
            raise ValueError(f"Subscription {subscription_id} not found")
        
        if not sub.get("stripe_subscription_id"):
            return sub  # Not synced to Stripe yet
        
        if immediately:
            stripe.Subscription.delete(sub["stripe_subscription_id"])
        else:
            stripe.Subscription.modify(
                sub["stripe_subscription_id"],
                cancel_at_period_end=True
            )
        
        return sub
    
    async def change_subscription_plan(
        self,
        conn,
        billing: BillingService,
        subscription_id: str,
        new_price_id: str,
        proration_behavior: str = "always_invoice",
    ) -> Dict[str, Any]:
        """
        Change subscription to a different plan/price.
        
        Handles upgrades and downgrades with proration.
        Also cancels any pending cancellation (reactivates).
        
        Args:
            subscription_id: Your subscription ID
            new_price_id: Your new price ID
            proration_behavior: How to handle proration
                - "always_invoice" (default): Charge/credit difference immediately
                - "create_prorations": Add to next invoice
                - "none": No proration, new price starts next period
        
        Returns:
            Updated subscription
        """
        sub = await billing.get_subscription(conn, subscription_id)
        if not sub:
            raise ValueError(f"Subscription {subscription_id} not found")
        
        old_price_id = sub.get("price_id")
        
        new_price = await billing.get_price(conn, new_price_id)
        if not new_price:
            raise ValueError(f"Price {new_price_id} not found")
        
        # Ensure price is synced to Stripe
        if not new_price.get("stripe_price_id"):
            new_price = await self.sync_price(conn, billing, new_price_id)
        
        if not sub.get("stripe_subscription_id"):
            raise ValueError("Subscription not synced to Stripe")
        
        # Get current Stripe subscription to find item ID
        stripe_sub = stripe.Subscription.retrieve(sub["stripe_subscription_id"])
        
        # Update the subscription item to new price
        updated_stripe_sub = stripe.Subscription.modify(
            sub["stripe_subscription_id"],
            items=[{
                "id": stripe_sub["items"]["data"][0]["id"],
                "price": new_price["stripe_price_id"],
            }],
            proration_behavior=proration_behavior,
            # Remove any pending cancellation
            cancel_at_period_end=False,
        )
        
        # Update local subscription
        sub["price_id"] = new_price_id
        sub["cancel_at_period_end"] = False
        sub["cancelled_at"] = None
        sub["status"] = updated_stripe_sub.status
        sub["current_period_start"] = datetime.fromtimestamp(
            updated_stripe_sub.current_period_start, tz=timezone.utc
        ).isoformat()
        sub["current_period_end"] = datetime.fromtimestamp(
            updated_stripe_sub.current_period_end, tz=timezone.utc
        ).isoformat()
        
        await conn.save_entity(
            BillingService.ENTITY_SUBSCRIPTION, 
            sub,
            comment=f"Plan changed: {old_price_id} → {new_price_id}"
        )
        
        return sub
    
    # ──────────────────────────────────────────────────────────────────
    # Payment Methods
    # ──────────────────────────────────────────────────────────────────
    
    async def attach_payment_method(
        self,
        conn,
        billing: BillingService,
        customer_id: str,
        payment_method_id: str,
        set_default: bool = True,
    ) -> Dict[str, Any]:
        """
        Attach a Stripe payment method to a customer.
        
        Args:
            conn: Database connection
            billing: BillingService instance
            customer_id: Your customer ID
            payment_method_id: Stripe PaymentMethod ID (from frontend)
            set_default: Whether to set as default payment method
        """
        customer = await billing.get_customer(conn, customer_id)
        if not customer:
            raise ValueError(f"Customer {customer_id} not found")
        
        # Ensure customer is synced
        if not customer.get("stripe_customer_id"):
            customer = await self.sync_customer(conn, billing, customer_id)
        
        # Attach payment method
        stripe.PaymentMethod.attach(
            payment_method_id,
            customer=customer["stripe_customer_id"],
        )
        
        if set_default:
            stripe.Customer.modify(
                customer["stripe_customer_id"],
                invoice_settings={"default_payment_method": payment_method_id},
            )
        
        # Store in your DB
        pm = {
            "id": payment_method_id,
            "customer_id": customer_id,
            "stripe_payment_method_id": payment_method_id,
            "is_default": set_default,
        }
        await conn.save_entity(BillingService.ENTITY_PAYMENT_METHOD, pm)
        
        return pm
    
    # ──────────────────────────────────────────────────────────────────
    # Checkout Session (All Product Types)
    # ──────────────────────────────────────────────────────────────────
    
    async def create_checkout_session(
        self,
        conn,
        billing: BillingService,
        customer_id: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
        mode: str = None,  # Auto-detect if None
        quantity: int = 1,
        collect_shipping: bool = None,  # Auto-detect if None
        shipping_countries: List[str] = None,  # e.g., ["US", "CA", "GB"]
        metadata: Dict[str, Any] = None,
    ) -> str:
        """
        Create a Stripe Checkout session.
        
        Supports subscriptions, one-time purchases, and physical products.
        
        Args:
            customer_id: Your customer ID
            price_id: Your price ID
            success_url: Redirect after successful payment
            cancel_url: Redirect if cancelled
            mode: "subscription" or "payment" (auto-detected from price)
            quantity: Number of items (default 1)
            collect_shipping: Collect shipping address (auto for physical)
            shipping_countries: Allowed shipping countries
            metadata: Additional metadata for the session
        
        Returns:
            Checkout session URL to redirect user to
        """
        customer = await billing.get_customer(conn, customer_id)
        if not customer:
            raise ValueError(f"Customer {customer_id} not found")
        
        if not customer.get("stripe_customer_id"):
            customer = await self.sync_customer(conn, billing, customer_id)
        
        price = await billing.get_price(conn, price_id)
        if not price:
            raise ValueError(f"Price {price_id} not found")
        
        if not price.get("stripe_price_id"):
            price = await self.sync_price(conn, billing, price_id)
        
        product = await billing.get_product(conn, price["product_id"])
        
        # Auto-detect mode from price
        if mode is None:
            if price.get("interval"):
                mode = "subscription"
            else:
                mode = "payment"
        
        # Auto-detect shipping from product type
        if collect_shipping is None:
            collect_shipping = product.get("shippable", False) or product.get("product_type") == "physical"
        
        # Build checkout params
        checkout_params = {
            "customer": customer["stripe_customer_id"],
            "line_items": [{"price": price["stripe_price_id"], "quantity": quantity}],
            "mode": mode,
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": {
                "local_customer_id": customer_id,
                "local_price_id": price_id,
                "local_product_id": price["product_id"],
                "product_type": product.get("product_type", "subscription"),
                **(metadata or {}),
            },
        }
        
        # Add shipping if needed
        if collect_shipping:
            checkout_params["shipping_address_collection"] = {
                "allowed_countries": shipping_countries or ["US", "CA", "GB", "AU", "DE", "FR", "NL"],
            }
        
        session = stripe.checkout.Session.create(**checkout_params)
        
        return session.url
    
    async def verify_checkout_session(
        self,
        conn,
        billing: BillingService,
        session_id: str,
    ) -> Dict[str, Any]:
        """
        Verify and fulfill a checkout session after redirect.
        
        Called when user returns from Stripe Checkout. Creates local
        subscription/order if payment succeeded.
        
        Args:
            session_id: Stripe checkout session ID (from redirect URL)
            
        Returns:
            Dict with status and created subscription/order
        """
        # Fetch session with expanded data
        session = stripe.checkout.Session.retrieve(
            session_id,
            expand=["subscription", "line_items", "customer"],
        )
        
        # Check payment status
        if session.payment_status != "paid" and session.status != "complete":
            return {
                "status": "pending",
                "payment_status": session.payment_status,
                "session_status": session.status,
            }
        
        # Get metadata (contains our local IDs)
        metadata = session.get("metadata", {})
        local_customer_id = metadata.get("local_customer_id")
        local_price_id = metadata.get("local_price_id")
        product_type = metadata.get("product_type", "subscription")
        
        result = {
            "status": "success",
            "mode": session.mode,
            "payment_status": session.payment_status,
        }
        
        # Handle subscription
        if session.mode == "subscription" and session.subscription:
            stripe_sub = session.subscription
            if isinstance(stripe_sub, str):
                stripe_sub = stripe.Subscription.retrieve(stripe_sub)
            
            # Check if we already have this subscription
            existing = await conn.find_entities(
                BillingService.ENTITY_SUBSCRIPTION,
                filters={"stripe_subscription_id": stripe_sub.id},
                limit=1,
            )
            
            if existing:
                result["subscription_id"] = existing[0]["id"]
                result["created"] = False
            else:
                # Create local subscription
                sub = {
                    "id": str(uuid.uuid4()),
                    "customer_id": local_customer_id,
                    "price_id": local_price_id,
                    "status": stripe_sub.status,
                    "stripe_subscription_id": stripe_sub.id,
                    "current_period_start": datetime.fromtimestamp(
                        stripe_sub.current_period_start, tz=timezone.utc
                    ).isoformat(),
                    "current_period_end": datetime.fromtimestamp(
                        stripe_sub.current_period_end, tz=timezone.utc
                    ).isoformat(),
                    "cancel_at_period_end": stripe_sub.cancel_at_period_end,
                    "metadata": {"created_from": "checkout_verify"},
                }
                
                await conn.save_entity(
                    BillingService.ENTITY_SUBSCRIPTION, 
                    sub,
                    comment=f"Subscription created from checkout (price: {local_price_id})"
                )
                result["subscription_id"] = sub["id"]
                result["created"] = True
        
        # Handle one-time payment (order)
        elif session.mode == "payment":
            # Check if order exists
            existing = await conn.find_entities(
                BillingService.ENTITY_ORDER,
                filters={"stripe_session_id": session.id},
                limit=1,
            )
            
            if existing:
                result["order_id"] = existing[0]["id"]
                result["created"] = False
            else:
                # Get line items for order details
                line_items = session.line_items.data if session.line_items else []
                
                order = {
                    "id": str(uuid.uuid4()),
                    "customer_id": local_customer_id,
                    "price_id": local_price_id,
                    "status": "paid",
                    "stripe_session_id": session.id,
                    "stripe_payment_intent_id": session.payment_intent,
                    "amount_total": session.amount_total,
                    "currency": session.currency,
                    "quantity": line_items[0].quantity if line_items else 1,
                    "shipping_address": dict(session.shipping_details) if session.shipping_details else None,
                    "metadata": {"created_from": "checkout_verify"},
                }
                
                await conn.save_entity(
                    BillingService.ENTITY_ORDER, 
                    order,
                    comment=f"Order created from checkout (price: {local_price_id})"
                )
                result["order_id"] = order["id"]
                result["created"] = True
        
        return result
    
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
        
        Lets customers manage their subscription, payment methods, etc.
        
        Returns:
            Portal URL to redirect user to
        """
        customer = await billing.get_customer(conn, customer_id)
        if not customer:
            raise ValueError(f"Customer {customer_id} not found")
        
        if not customer.get("stripe_customer_id"):
            raise ValueError(f"Customer {customer_id} not synced to Stripe")
        
        session = stripe.billing_portal.Session.create(
            customer=customer["stripe_customer_id"],
            return_url=return_url,
        )
        
        return session.url
    
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
        
        stripe_sub = stripe.Subscription.retrieve(sub["stripe_subscription_id"])
        
        # Update local state from Stripe
        sub["status"] = stripe_sub.status
        sub["current_period_start"] = datetime.fromtimestamp(
            stripe_sub.current_period_start, tz=timezone.utc
        ).isoformat()
        sub["current_period_end"] = datetime.fromtimestamp(
            stripe_sub.current_period_end, tz=timezone.utc
        ).isoformat()
        sub["cancel_at_period_end"] = stripe_sub.cancel_at_period_end
        
        if stripe_sub.canceled_at:
            sub["cancelled_at"] = datetime.fromtimestamp(
                stripe_sub.canceled_at, tz=timezone.utc
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
        # Fetch session from Stripe with expanded data
        session = stripe.checkout.Session.retrieve(
            session_id,
            expand=["subscription", "line_items"]
        )
        
        # Verify payment completed
        if session.payment_status != "paid" and session.status != "complete":
            return {
                "status": "pending",
                "payment_status": session.payment_status,
                "session_status": session.status,
            }
        
        metadata = session.get("metadata", {})
        local_customer_id = metadata.get("local_customer_id")
        local_price_id = metadata.get("local_price_id")
        product_type = metadata.get("product_type", "subscription")
        
        if not local_customer_id or not local_price_id:
            return {"status": "error", "error": "Missing metadata in session"}
        
        # Handle subscription mode
        if session.mode == "subscription" and session.subscription:
            # Check if we already have this subscription
            existing = await conn.find_entities(
                BillingService.ENTITY_SUBSCRIPTION,
                filters={"stripe_subscription_id": session.subscription},
                limit=1
            )
            
            if existing:
                # Already processed (maybe via webhook)
                return {
                    "status": "exists",
                    "subscription_id": existing[0]["id"],
                }
            
            # Fetch full subscription details from Stripe
            stripe_sub = stripe.Subscription.retrieve(session.subscription)
            
            # Create local subscription
            import uuid
            sub = {
                "id": str(uuid.uuid4()),
                "customer_id": local_customer_id,
                "price_id": local_price_id,
                "status": stripe_sub.status,
                "stripe_subscription_id": session.subscription,
                "current_period_start": datetime.fromtimestamp(
                    stripe_sub.current_period_start, tz=timezone.utc
                ).isoformat(),
                "current_period_end": datetime.fromtimestamp(
                    stripe_sub.current_period_end, tz=timezone.utc
                ).isoformat(),
                "cancel_at_period_end": stripe_sub.cancel_at_period_end,
            }
            
            await conn.save_entity(BillingService.ENTITY_SUBSCRIPTION, sub)
            
            return {
                "status": "created",
                "type": "subscription",
                "subscription_id": sub["id"],
            }
        
        # Handle one-time payment mode
        elif session.mode == "payment":
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
            import uuid
            order = {
                "id": str(uuid.uuid4()),
                "customer_id": local_customer_id,
                "price_id": local_price_id,
                "status": "paid",
                "amount_cents": price["amount_cents"] if price else session.amount_total,
                "currency": price["currency"] if price else session.currency,
                "stripe_session_id": session_id,
                "stripe_payment_intent_id": session.payment_intent,
                "product_type": product_type,
            }
            
            # Add shipping if collected
            if session.shipping_details:
                order["shipping_name"] = session.shipping_details.get("name")
                order["shipping_address"] = session.shipping_details.get("address", {})
            
            await conn.save_entity(BillingService.ENTITY_ORDER, order)
            
            return {
                "status": "created",
                "type": "order",
                "order_id": order["id"],
            }
        
        return {"status": "unknown_mode", "mode": session.mode}
