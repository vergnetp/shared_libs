"""
Webhook handlers for Stripe events.

Stripe sends events, we update our DB (the golden source).

Note: Uses Stripe SDK for webhook signature verification only.
SDK import is lazy (inside functions) to avoid loading when webhooks aren't used.
"""

from typing import Dict, Any, Optional, Callable
from datetime import datetime, timezone

from ..config import BillingConfig
from ..services.billing_service import BillingService, SubscriptionStatus


class WebhookHandler:
    """
    Handles Stripe webhook events.
    
    Connection is injected - you control the lifecycle.
    
    Usage with FastAPI:
        from databases import DatabaseManager
        
        handler = WebhookHandler(config)
        billing = BillingService(config)
        
        @app.post("/webhooks/stripe")
        async def stripe_webhook(request: Request):
            payload = await request.body()
            sig = request.headers.get("stripe-signature")
            
            async with DatabaseManager.connect("postgres", **db_config) as conn:
                result = await handler.handle(conn, payload, sig, billing)
            
            return {"received": True}
    """
    
    def __init__(self, config: BillingConfig):
        self.config = config
        
        # Event handlers registry
        self._handlers: Dict[str, Callable] = {
            # Subscription events
            "customer.subscription.created": self._handle_subscription_created,
            "customer.subscription.updated": self._handle_subscription_updated,
            "customer.subscription.deleted": self._handle_subscription_deleted,
            "customer.subscription.trial_will_end": self._handle_trial_will_end,
            
            # Invoice events
            "invoice.payment_succeeded": self._handle_invoice_paid,
            "invoice.payment_failed": self._handle_invoice_failed,
            "invoice.created": self._handle_invoice_created,
            "invoice.finalized": self._handle_invoice_finalized,
            
            # Customer events
            "customer.created": self._handle_customer_created,
            "customer.updated": self._handle_customer_updated,
            
            # Payment method events
            "payment_method.attached": self._handle_payment_method_attached,
            "payment_method.detached": self._handle_payment_method_detached,
            
            # Checkout events
            "checkout.session.completed": self._handle_checkout_completed,
        }
    
    def verify_signature(self, payload: bytes, signature: str):
        """
        Verify webhook signature and construct event.
        
        Raises:
            stripe.error.SignatureVerificationError: If signature is invalid
        """
        # Lazy import - only load SDK when webhooks are actually used
        import stripe
        stripe.api_key = self.config.stripe.secret_key
        
        return stripe.Webhook.construct_event(
            payload,
            signature,
            self.config.stripe.webhook_secret,
            tolerance=self.config.webhook_tolerance,
        )
    
    async def handle(
        self,
        conn,
        payload: bytes,
        signature: str,
        billing: BillingService,
    ) -> Dict[str, Any]:
        """
        Handle a webhook event.
        
        Args:
            conn: Database connection
            payload: Raw request body
            signature: Stripe-Signature header value
            billing: BillingService instance
            
        Returns:
            Dict with handling result
        """
        try:
            event = self.verify_signature(payload, signature)
        except Exception as e:
            # Catch SignatureVerificationError without importing at module level
            error_name = type(e).__name__
            if "SignatureVerification" in error_name:
                return {"status": "error", "error": "Invalid signature", "detail": str(e)}
            raise
        
        event_type = event.type
        event_data = event.data.object
        
        handler = self._handlers.get(event_type)
        if handler:
            try:
                result = await handler(conn, event_data, billing)
                return {
                    "status": "handled",
                    "event_type": event_type,
                    "event_id": event.id,
                    "result": result,
                }
            except Exception as e:
                return {
                    "status": "error",
                    "event_type": event_type,
                    "event_id": event.id,
                    "error": str(e),
                }
        else:
            return {
                "status": "ignored",
                "event_type": event_type,
                "event_id": event.id,
            }
    
    # ──────────────────────────────────────────────────────────────────
    # Subscription Handlers
    # ──────────────────────────────────────────────────────────────────
    
    async def _handle_subscription_created(
        self,
        conn,
        stripe_sub: Dict[str, Any],
        billing: BillingService,
    ) -> Dict[str, Any]:
        """Handle subscription.created event."""
        local_id = stripe_sub.get("metadata", {}).get("local_id")
        
        if local_id:
            sub = await billing.get_subscription(conn, local_id)
        else:
            subs = await conn.find_entities(
                BillingService.ENTITY_SUBSCRIPTION,
                filters={"stripe_subscription_id": stripe_sub["id"]},
                limit=1
            )
            sub = subs[0] if subs else None
        
        if not sub:
            sub = await self._create_local_subscription(conn, stripe_sub, billing)
        
        return {"subscription_id": sub["id"], "action": "created"}
    
    async def _handle_subscription_updated(
        self,
        conn,
        stripe_sub: Dict[str, Any],
        billing: BillingService,
    ) -> Dict[str, Any]:
        """Handle subscription.updated event."""
        sub = await self._find_subscription_by_stripe_id(conn, stripe_sub["id"])
        if not sub:
            return {"action": "ignored", "reason": "subscription not found"}
        
        sub["status"] = stripe_sub["status"]
        sub["current_period_start"] = datetime.fromtimestamp(
            stripe_sub["current_period_start"], tz=timezone.utc
        ).isoformat()
        sub["current_period_end"] = datetime.fromtimestamp(
            stripe_sub["current_period_end"], tz=timezone.utc
        ).isoformat()
        sub["cancel_at_period_end"] = stripe_sub.get("cancel_at_period_end", False)
        
        if stripe_sub.get("canceled_at"):
            sub["cancelled_at"] = datetime.fromtimestamp(
                stripe_sub["canceled_at"], tz=timezone.utc
            ).isoformat()
        
        await conn.save_entity(BillingService.ENTITY_SUBSCRIPTION, sub)
        
        return {"subscription_id": sub["id"], "action": "updated", "status": sub["status"]}
    
    async def _handle_subscription_deleted(
        self,
        conn,
        stripe_sub: Dict[str, Any],
        billing: BillingService,
    ) -> Dict[str, Any]:
        """Handle subscription.deleted event."""
        sub = await self._find_subscription_by_stripe_id(conn, stripe_sub["id"])
        if not sub:
            return {"action": "ignored", "reason": "subscription not found"}
        
        sub["status"] = SubscriptionStatus.CANCELLED.value
        sub["cancelled_at"] = datetime.now(timezone.utc).isoformat()
        
        await conn.save_entity(BillingService.ENTITY_SUBSCRIPTION, sub)
        
        return {"subscription_id": sub["id"], "action": "deleted"}
    
    async def _handle_trial_will_end(
        self,
        conn,
        stripe_sub: Dict[str, Any],
        billing: BillingService,
    ) -> Dict[str, Any]:
        """Handle subscription.trial_will_end event."""
        sub = await self._find_subscription_by_stripe_id(conn, stripe_sub["id"])
        if not sub:
            return {"action": "ignored", "reason": "subscription not found"}
        
        # Just log - actual status change comes from subscription.updated
        return {
            "subscription_id": sub["id"],
            "action": "trial_ending",
            "trial_end": stripe_sub.get("trial_end"),
        }
    
    # ──────────────────────────────────────────────────────────────────
    # Invoice Handlers
    # ──────────────────────────────────────────────────────────────────
    
    async def _handle_invoice_paid(
        self,
        conn,
        stripe_invoice: Dict[str, Any],
        billing: BillingService,
    ) -> Dict[str, Any]:
        """Handle invoice.payment_succeeded event."""
        invoice = await self._upsert_invoice(conn, stripe_invoice, billing)
        return {"invoice_id": invoice["id"], "action": "paid"}
    
    async def _handle_invoice_failed(
        self,
        conn,
        stripe_invoice: Dict[str, Any],
        billing: BillingService,
    ) -> Dict[str, Any]:
        """Handle invoice.payment_failed event."""
        invoice = await self._upsert_invoice(conn, stripe_invoice, billing)
        
        # Mark subscription as past_due if applicable
        if stripe_invoice.get("subscription"):
            sub = await self._find_subscription_by_stripe_id(
                conn, stripe_invoice["subscription"]
            )
            if sub:
                sub["status"] = SubscriptionStatus.PAST_DUE.value
                await conn.save_entity(BillingService.ENTITY_SUBSCRIPTION, sub)
        
        return {"invoice_id": invoice["id"], "action": "payment_failed"}
    
    async def _handle_invoice_created(
        self,
        conn,
        stripe_invoice: Dict[str, Any],
        billing: BillingService,
    ) -> Dict[str, Any]:
        """Handle invoice.created event."""
        invoice = await self._upsert_invoice(conn, stripe_invoice, billing)
        return {"invoice_id": invoice["id"], "action": "created"}
    
    async def _handle_invoice_finalized(
        self,
        conn,
        stripe_invoice: Dict[str, Any],
        billing: BillingService,
    ) -> Dict[str, Any]:
        """Handle invoice.finalized event."""
        invoice = await self._upsert_invoice(conn, stripe_invoice, billing)
        return {"invoice_id": invoice["id"], "action": "finalized"}
    
    # ──────────────────────────────────────────────────────────────────
    # Customer Handlers
    # ──────────────────────────────────────────────────────────────────
    
    async def _handle_customer_created(
        self,
        conn,
        stripe_customer: Dict[str, Any],
        billing: BillingService,
    ) -> Dict[str, Any]:
        """Handle customer.created event (usually our sync created it)."""
        local_id = stripe_customer.get("metadata", {}).get("local_id")
        if local_id:
            customer = await billing.get_customer(conn, local_id)
            if customer:
                return {"customer_id": customer["id"], "action": "already_exists"}
        
        # Customer created in Stripe directly (not through our sync)
        return {"action": "external_customer", "stripe_id": stripe_customer["id"]}
    
    async def _handle_customer_updated(
        self,
        conn,
        stripe_customer: Dict[str, Any],
        billing: BillingService,
    ) -> Dict[str, Any]:
        """Handle customer.updated event."""
        customer = await self._find_customer_by_stripe_id(conn, stripe_customer["id"])
        if not customer:
            return {"action": "ignored", "reason": "customer not found"}
        
        # Update email if changed in Stripe
        if stripe_customer.get("email") and stripe_customer["email"] != customer.get("email"):
            customer["email"] = stripe_customer["email"]
            await conn.save_entity(BillingService.ENTITY_CUSTOMER, customer)
            return {"customer_id": customer["id"], "action": "email_updated"}
        
        return {"customer_id": customer["id"], "action": "no_changes"}
    
    # ──────────────────────────────────────────────────────────────────
    # Payment Method Handlers
    # ──────────────────────────────────────────────────────────────────
    
    async def _handle_payment_method_attached(
        self,
        conn,
        stripe_pm: Dict[str, Any],
        billing: BillingService,
    ) -> Dict[str, Any]:
        """Handle payment_method.attached event."""
        if not stripe_pm.get("customer"):
            return {"action": "ignored", "reason": "no customer"}
        
        customer = await self._find_customer_by_stripe_id(conn, stripe_pm["customer"])
        if not customer:
            return {"action": "ignored", "reason": "customer not found"}
        
        pm = {
            "id": stripe_pm["id"],
            "customer_id": customer["id"],
            "stripe_payment_method_id": stripe_pm["id"],
            "type": stripe_pm.get("type"),
            "card_last4": stripe_pm.get("card", {}).get("last4"),
            "card_brand": stripe_pm.get("card", {}).get("brand"),
        }
        await conn.save_entity(BillingService.ENTITY_PAYMENT_METHOD, pm)
        
        return {"payment_method_id": pm["id"], "action": "attached"}
    
    async def _handle_payment_method_detached(
        self,
        conn,
        stripe_pm: Dict[str, Any],
        billing: BillingService,
    ) -> Dict[str, Any]:
        """Handle payment_method.detached event."""
        pm = await conn.get_entity(
            BillingService.ENTITY_PAYMENT_METHOD,
            stripe_pm["id"]
        )
        if pm:
            await conn.delete_entity(BillingService.ENTITY_PAYMENT_METHOD, pm["id"])
            return {"payment_method_id": pm["id"], "action": "detached"}
        
        return {"action": "ignored", "reason": "payment method not found"}
    
    # ──────────────────────────────────────────────────────────────────
    # Checkout Handlers
    # ──────────────────────────────────────────────────────────────────
    
    async def _handle_checkout_completed(
        self,
        conn,
        session: Dict[str, Any],
        billing: BillingService,
    ) -> Dict[str, Any]:
        """Handle checkout.session.completed event (idempotent)."""
        if session.get("subscription"):
            stripe_sub_id = session["subscription"]
            
            # Check if subscription already exists (idempotency)
            existing = await self._find_subscription_by_stripe_id(conn, stripe_sub_id)
            if existing:
                return {
                    "subscription_id": existing["id"],
                    "action": "already_exists",
                    "source": "checkout_completed",
                }
            
            local_customer_id = session.get("metadata", {}).get("local_customer_id")
            local_price_id = session.get("metadata", {}).get("local_price_id")
            
            if local_customer_id and local_price_id:
                import uuid
                sub = {
                    "id": str(uuid.uuid4()),
                    "customer_id": local_customer_id,
                    "price_id": local_price_id,
                    "status": SubscriptionStatus.ACTIVE.value,
                    "stripe_subscription_id": stripe_sub_id,
                    "current_period_start": datetime.now(timezone.utc).isoformat(),
                    "current_period_end": datetime.now(timezone.utc).isoformat(),
                    "cancel_at_period_end": False,
                }
                await conn.save_entity(
                    BillingService.ENTITY_SUBSCRIPTION, 
                    sub,
                    comment=f"Subscription created from webhook (price: {local_price_id})"
                )
                
                return {"subscription_id": sub["id"], "action": "created_from_checkout"}
        
        return {"action": "checkout_completed", "mode": session.get("mode")}
    
    # ──────────────────────────────────────────────────────────────────
    # Helper Methods
    # ──────────────────────────────────────────────────────────────────
    
    async def _find_subscription_by_stripe_id(
        self,
        conn,
        stripe_subscription_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Find local subscription by Stripe ID."""
        subs = await conn.find_entities(
            BillingService.ENTITY_SUBSCRIPTION,
            filters={"stripe_subscription_id": stripe_subscription_id},
            limit=1
        )
        return subs[0] if subs else None
    
    async def _find_customer_by_stripe_id(
        self,
        conn,
        stripe_customer_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Find local customer by Stripe ID."""
        customers = await conn.find_entities(
            BillingService.ENTITY_CUSTOMER,
            filters={"stripe_customer_id": stripe_customer_id},
            limit=1
        )
        return customers[0] if customers else None
    
    async def _upsert_invoice(
        self,
        conn,
        stripe_invoice: Dict[str, Any],
        billing: BillingService,
    ) -> Dict[str, Any]:
        """Create or update invoice from Stripe data."""
        invoices = await conn.find_entities(
            BillingService.ENTITY_INVOICE,
            filters={"stripe_invoice_id": stripe_invoice["id"]},
            limit=1
        )
        
        if invoices:
            invoice = invoices[0]
        else:
            import uuid
            invoice = {"id": str(uuid.uuid4())}
        
        customer = None
        if stripe_invoice.get("customer"):
            customer = await self._find_customer_by_stripe_id(
                conn, stripe_invoice["customer"]
            )
        
        subscription = None
        if stripe_invoice.get("subscription"):
            subscription = await self._find_subscription_by_stripe_id(
                conn, stripe_invoice["subscription"]
            )
        
        invoice.update({
            "stripe_invoice_id": stripe_invoice["id"],
            "customer_id": customer["id"] if customer else None,
            "subscription_id": subscription["id"] if subscription else None,
            "status": stripe_invoice.get("status"),
            "amount_due": stripe_invoice.get("amount_due"),
            "amount_paid": stripe_invoice.get("amount_paid"),
            "currency": stripe_invoice.get("currency"),
            "invoice_pdf": stripe_invoice.get("invoice_pdf"),
            "hosted_invoice_url": stripe_invoice.get("hosted_invoice_url"),
            "period_start": datetime.fromtimestamp(
                stripe_invoice["period_start"], tz=timezone.utc
            ).isoformat() if stripe_invoice.get("period_start") else None,
            "period_end": datetime.fromtimestamp(
                stripe_invoice["period_end"], tz=timezone.utc
            ).isoformat() if stripe_invoice.get("period_end") else None,
        })
        
        await conn.save_entity(BillingService.ENTITY_INVOICE, invoice)
        return invoice
    
    async def _create_local_subscription(
        self,
        conn,
        stripe_sub: Dict[str, Any],
        billing: BillingService,
    ) -> Dict[str, Any]:
        """Create local subscription from Stripe subscription."""
        import uuid
        
        customer = None
        if stripe_sub.get("customer"):
            customer = await self._find_customer_by_stripe_id(
                conn, stripe_sub["customer"]
            )
        
        price_id = None
        if stripe_sub.get("items", {}).get("data"):
            stripe_price_id = stripe_sub["items"]["data"][0]["price"]["id"]
            prices = await conn.find_entities(
                BillingService.ENTITY_PRICE,
                filters={"stripe_price_id": stripe_price_id},
                limit=1
            )
            if prices:
                price_id = prices[0]["id"]
        
        sub = {
            "id": str(uuid.uuid4()),
            "customer_id": customer["id"] if customer else None,
            "price_id": price_id,
            "status": stripe_sub.get("status"),
            "stripe_subscription_id": stripe_sub["id"],
            "current_period_start": datetime.fromtimestamp(
                stripe_sub["current_period_start"], tz=timezone.utc
            ).isoformat(),
            "current_period_end": datetime.fromtimestamp(
                stripe_sub["current_period_end"], tz=timezone.utc
            ).isoformat(),
            "cancel_at_period_end": stripe_sub.get("cancel_at_period_end", False),
            "metadata": {"created_from_webhook": True},
        }
        
        await conn.save_entity(
            BillingService.ENTITY_SUBSCRIPTION, 
            sub,
            comment=f"Subscription synced from Stripe webhook (price: {price_id})"
        )
        return sub
