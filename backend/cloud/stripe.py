"""
Stripe Client - Payment processing API.

Sync and async clients with retry, circuit breaker, and tracing.

Replaces direct Stripe SDK usage for API calls.
Webhook signature verification should still use the SDK.

Usage:
    # Sync
    from cloud import StripeClient
    
    client = StripeClient(api_key="sk_...")
    product = client.create_product(name="Pro Plan")
    
    # Async
    from cloud import AsyncStripeClient
    
    async with AsyncStripeClient(api_key="sk_...") as client:
        product = await client.create_product(name="Pro Plan")
"""

from __future__ import annotations
import uuid
from typing import Dict, Any, List, Optional

from .base import BaseCloudClient, AsyncBaseCloudClient, CloudClientConfig
from .errors import StripeError


# =============================================================================
# Sync Client
# =============================================================================

class StripeClient(BaseCloudClient):
    """
    Stripe API client (sync).
    
    Usage:
        client = StripeClient(api_key="sk_...")
        
        # Create product
        product = client.create_product(
            name="Pro Plan",
            description="Full access",
            metadata={"local_id": "xxx"},
        )
        
        # Create price
        price = client.create_price(
            product_id=product["id"],
            unit_amount=1999,
            currency="usd",
            recurring={"interval": "month"},
        )
        
        # Create checkout session
        session = client.create_checkout_session(
            customer="cus_xxx",
            line_items=[{"price": price["id"], "quantity": 1}],
            mode="subscription",
            success_url="https://...",
            cancel_url="https://...",
        )
    """
    
    PROVIDER = "Stripe"
    BASE_URL = "https://api.stripe.com/v1"
    API_VERSION = "2023-10-16"
    
    def __init__(
        self,
        api_key: str,
        api_version: str = None,
        config: CloudClientConfig = None,
    ):
        # Don't call super().__init__ - we need custom auth
        self.api_key = api_key
        self.api_version = api_version or self.API_VERSION
        self.config = config or CloudClientConfig()
        
        from .base import default_http_config
        from ..http_client import SyncHttpClient
        
        http_config = default_http_config(
            self.config,
            circuit_breaker_name="stripe-api",
        )
        
        self._client = SyncHttpClient(
            config=http_config,
            base_url=self.BASE_URL,
            circuit_breaker_name="stripe-api",
        )
        # Stripe uses Basic auth with API key as username
        self._client.set_auth_header("Bearer", api_key)
    
    # =========================================================================
    # HTTP Helpers
    # =========================================================================
    
    def _request(
        self,
        method: str,
        path: str,
        data: Dict = None,
        params: Dict = None,
    ) -> Dict[str, Any]:
        """Make API request with form-encoded body."""
        headers = {
            "Stripe-Version": self.api_version,
        }
        
        # Generate idempotency key for POST requests
        if method == "POST":
            headers["Idempotency-Key"] = str(uuid.uuid4())
        
        # Stripe uses form-encoded, not JSON
        form_data = self._flatten_params(data) if data else None
        
        response = self._client.request(
            method=method,
            url=path,
            data=form_data,
            params=params,
            headers=headers,
            raise_on_error=False,
        )
        
        result = response.json() if response.body else {}
        
        if response.status_code >= 400:
            error = result.get("error", {})
            raise StripeError(
                message=error.get("message", str(result)),
                status_code=response.status_code,
                error_type=error.get("type"),
                error_code=error.get("code"),
                param=error.get("param"),
            )
        
        return result
    
    def _flatten_params(self, params: Dict, prefix: str = "") -> Dict[str, str]:
        """Flatten nested dict for form encoding (Stripe style)."""
        result = {}
        
        for key, value in params.items():
            full_key = f"{prefix}[{key}]" if prefix else key
            
            if value is None:
                continue
            elif isinstance(value, dict):
                result.update(self._flatten_params(value, full_key))
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, dict):
                        result.update(self._flatten_params(item, f"{full_key}[{i}]"))
                    else:
                        result[f"{full_key}[{i}]"] = str(item)
            elif isinstance(value, bool):
                result[full_key] = "true" if value else "false"
            else:
                result[full_key] = str(value)
        
        return result
    
    # =========================================================================
    # Products
    # =========================================================================
    
    def create_product(
        self,
        name: str,
        description: str = None,
        active: bool = True,
        metadata: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        """Create a product."""
        data = {
            "name": name,
            "active": active,
        }
        if description:
            data["description"] = description
        if metadata:
            data["metadata"] = metadata
        
        return self._request("POST", "/products", data=data)
    
    def modify_product(
        self,
        product_id: str,
        name: str = None,
        description: str = None,
        active: bool = None,
        metadata: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        """Update a product."""
        data = {}
        if name is not None:
            data["name"] = name
        if description is not None:
            data["description"] = description
        if active is not None:
            data["active"] = active
        if metadata is not None:
            data["metadata"] = metadata
        
        return self._request("POST", f"/products/{product_id}", data=data)
    
    def retrieve_product(self, product_id: str) -> Dict[str, Any]:
        """Get a product."""
        return self._request("GET", f"/products/{product_id}")
    
    def list_products(
        self,
        active: bool = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """List products."""
        params = {"limit": limit}
        if active is not None:
            params["active"] = "true" if active else "false"
        return self._request("GET", "/products", params=params)
    
    # =========================================================================
    # Prices
    # =========================================================================
    
    def create_price(
        self,
        product: str,
        unit_amount: int,
        currency: str = "usd",
        recurring: Dict[str, Any] = None,
        nickname: str = None,
        active: bool = True,
        metadata: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        """
        Create a price.
        
        Args:
            product: Product ID
            unit_amount: Amount in cents (1999 = $19.99)
            currency: ISO currency code
            recurring: {"interval": "month", "interval_count": 1} or None for one-time
            nickname: Internal name
            active: Whether price is available
            metadata: Additional data
        """
        data = {
            "product": product,
            "unit_amount": unit_amount,
            "currency": currency,
            "active": active,
        }
        if recurring:
            data["recurring"] = recurring
        if nickname:
            data["nickname"] = nickname
        if metadata:
            data["metadata"] = metadata
        
        return self._request("POST", "/prices", data=data)
    
    def modify_price(
        self,
        price_id: str,
        active: bool = None,
        nickname: str = None,
        metadata: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        """Update a price (limited - prices are mostly immutable)."""
        data = {}
        if active is not None:
            data["active"] = active
        if nickname is not None:
            data["nickname"] = nickname
        if metadata is not None:
            data["metadata"] = metadata
        
        return self._request("POST", f"/prices/{price_id}", data=data)
    
    def retrieve_price(self, price_id: str) -> Dict[str, Any]:
        """Get a price."""
        return self._request("GET", f"/prices/{price_id}")
    
    # =========================================================================
    # Customers
    # =========================================================================
    
    def create_customer(
        self,
        email: str = None,
        name: str = None,
        metadata: Dict[str, str] = None,
        payment_method: str = None,
        invoice_settings: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """Create a customer."""
        data = {}
        if email:
            data["email"] = email
        if name:
            data["name"] = name
        if metadata:
            data["metadata"] = metadata
        if payment_method:
            data["payment_method"] = payment_method
        if invoice_settings:
            data["invoice_settings"] = invoice_settings
        
        return self._request("POST", "/customers", data=data)
    
    def modify_customer(
        self,
        customer_id: str,
        email: str = None,
        name: str = None,
        metadata: Dict[str, str] = None,
        invoice_settings: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """Update a customer."""
        data = {}
        if email is not None:
            data["email"] = email
        if name is not None:
            data["name"] = name
        if metadata is not None:
            data["metadata"] = metadata
        if invoice_settings is not None:
            data["invoice_settings"] = invoice_settings
        
        return self._request("POST", f"/customers/{customer_id}", data=data)
    
    def retrieve_customer(self, customer_id: str) -> Dict[str, Any]:
        """Get a customer."""
        return self._request("GET", f"/customers/{customer_id}")
    
    def delete_customer(self, customer_id: str) -> Dict[str, Any]:
        """Delete a customer."""
        return self._request("DELETE", f"/customers/{customer_id}")
    
    # =========================================================================
    # Subscriptions
    # =========================================================================
    
    def create_subscription(
        self,
        customer: str,
        items: List[Dict[str, Any]],
        default_payment_method: str = None,
        trial_end: int = None,
        metadata: Dict[str, str] = None,
        cancel_at_period_end: bool = False,
    ) -> Dict[str, Any]:
        """
        Create a subscription.
        
        Args:
            customer: Customer ID
            items: [{"price": "price_xxx", "quantity": 1}]
            default_payment_method: Payment method ID
            trial_end: Unix timestamp for trial end
            metadata: Additional data
            cancel_at_period_end: Cancel at end of period
        """
        data = {
            "customer": customer,
            "items": items,
            "cancel_at_period_end": cancel_at_period_end,
        }
        if default_payment_method:
            data["default_payment_method"] = default_payment_method
        if trial_end:
            data["trial_end"] = trial_end
        if metadata:
            data["metadata"] = metadata
        
        return self._request("POST", "/subscriptions", data=data)
    
    def modify_subscription(
        self,
        subscription_id: str,
        items: List[Dict[str, Any]] = None,
        cancel_at_period_end: bool = None,
        proration_behavior: str = None,
        metadata: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        """
        Update a subscription.
        
        Args:
            subscription_id: Subscription ID
            items: New items (for plan changes)
            cancel_at_period_end: Set pending cancellation
            proration_behavior: "create_prorations", "always_invoice", "none"
            metadata: Additional data
        """
        data = {}
        if items is not None:
            data["items"] = items
        if cancel_at_period_end is not None:
            data["cancel_at_period_end"] = cancel_at_period_end
        if proration_behavior is not None:
            data["proration_behavior"] = proration_behavior
        if metadata is not None:
            data["metadata"] = metadata
        
        return self._request("POST", f"/subscriptions/{subscription_id}", data=data)
    
    def retrieve_subscription(
        self,
        subscription_id: str,
        expand: List[str] = None,
    ) -> Dict[str, Any]:
        """Get a subscription."""
        params = {}
        if expand:
            params["expand[]"] = expand
        return self._request("GET", f"/subscriptions/{subscription_id}", params=params)
    
    def cancel_subscription(
        self,
        subscription_id: str,
        immediately: bool = False,
    ) -> Dict[str, Any]:
        """
        Cancel a subscription.
        
        Args:
            subscription_id: Subscription ID
            immediately: If True, cancel now. If False, cancel at period end.
        """
        if immediately:
            return self._request("DELETE", f"/subscriptions/{subscription_id}")
        else:
            return self.modify_subscription(subscription_id, cancel_at_period_end=True)
    
    # =========================================================================
    # Payment Methods
    # =========================================================================
    
    def attach_payment_method(
        self,
        payment_method_id: str,
        customer: str,
    ) -> Dict[str, Any]:
        """Attach a payment method to a customer."""
        return self._request(
            "POST",
            f"/payment_methods/{payment_method_id}/attach",
            data={"customer": customer},
        )
    
    def detach_payment_method(self, payment_method_id: str) -> Dict[str, Any]:
        """Detach a payment method from its customer."""
        return self._request("POST", f"/payment_methods/{payment_method_id}/detach")
    
    def retrieve_payment_method(self, payment_method_id: str) -> Dict[str, Any]:
        """Get a payment method."""
        return self._request("GET", f"/payment_methods/{payment_method_id}")
    
    def list_payment_methods(
        self,
        customer: str,
        type: str = "card",
    ) -> Dict[str, Any]:
        """List customer's payment methods."""
        return self._request(
            "GET",
            "/payment_methods",
            params={"customer": customer, "type": type},
        )
    
    # =========================================================================
    # Checkout Sessions
    # =========================================================================
    
    def create_checkout_session(
        self,
        customer: str,
        line_items: List[Dict[str, Any]],
        mode: str,
        success_url: str,
        cancel_url: str,
        metadata: Dict[str, str] = None,
        shipping_address_collection: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        Create a Checkout Session.
        
        Args:
            customer: Customer ID
            line_items: [{"price": "price_xxx", "quantity": 1}]
            mode: "subscription" or "payment"
            success_url: Redirect URL on success
            cancel_url: Redirect URL on cancel
            metadata: Additional data
            shipping_address_collection: {"allowed_countries": ["US", "CA"]}
        """
        data = {
            "customer": customer,
            "line_items": line_items,
            "mode": mode,
            "success_url": success_url,
            "cancel_url": cancel_url,
        }
        if metadata:
            data["metadata"] = metadata
        if shipping_address_collection:
            data["shipping_address_collection"] = shipping_address_collection
        
        return self._request("POST", "/checkout/sessions", data=data)
    
    def retrieve_checkout_session(
        self,
        session_id: str,
        expand: List[str] = None,
    ) -> Dict[str, Any]:
        """
        Get a Checkout Session.
        
        Args:
            session_id: Session ID
            expand: ["subscription", "line_items", "customer"]
        """
        params = {}
        if expand:
            for i, item in enumerate(expand):
                params[f"expand[{i}]"] = item
        return self._request("GET", f"/checkout/sessions/{session_id}", params=params)
    
    # =========================================================================
    # Billing Portal
    # =========================================================================
    
    def create_portal_session(
        self,
        customer: str,
        return_url: str,
    ) -> Dict[str, Any]:
        """
        Create a Customer Portal session.
        
        Returns session with URL to redirect customer to.
        """
        return self._request(
            "POST",
            "/billing_portal/sessions",
            data={"customer": customer, "return_url": return_url},
        )
    
    # =========================================================================
    # Invoices
    # =========================================================================
    
    def retrieve_invoice(self, invoice_id: str) -> Dict[str, Any]:
        """Get an invoice."""
        return self._request("GET", f"/invoices/{invoice_id}")
    
    def pay_invoice(
        self,
        invoice_id: str,
        payment_method: str = None,
    ) -> Dict[str, Any]:
        """Pay an invoice."""
        data = {}
        if payment_method:
            data["payment_method"] = payment_method
        return self._request("POST", f"/invoices/{invoice_id}/pay", data=data or None)
    
    def list_invoices(
        self,
        customer: str = None,
        subscription: str = None,
        status: str = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """List invoices."""
        params = {"limit": limit}
        if customer:
            params["customer"] = customer
        if subscription:
            params["subscription"] = subscription
        if status:
            params["status"] = status
        return self._request("GET", "/invoices", params=params)


# =============================================================================
# Async Client
# =============================================================================

class AsyncStripeClient(AsyncBaseCloudClient):
    """
    Stripe API client (async).
    
    Usage:
        async with AsyncStripeClient(api_key="sk_...") as client:
            product = await client.create_product(name="Pro Plan")
    """
    
    PROVIDER = "Stripe"
    BASE_URL = "https://api.stripe.com/v1"
    API_VERSION = "2023-10-16"
    
    def __init__(
        self,
        api_key: str,
        api_version: str = None,
        config: CloudClientConfig = None,
    ):
        self.api_key = api_key
        self.api_version = api_version or self.API_VERSION
        self.config = config or CloudClientConfig()
        
        from .base import default_http_config
        from ..http_client import AsyncHttpClient
        
        http_config = default_http_config(
            self.config,
            circuit_breaker_name="stripe-api-async",
        )
        
        self._client = AsyncHttpClient(
            config=http_config,
            base_url=self.BASE_URL,
            circuit_breaker_name="stripe-api-async",
        )
        self._client.set_bearer_token(api_key)
    
    # =========================================================================
    # HTTP Helpers
    # =========================================================================
    
    async def _request(
        self,
        method: str,
        path: str,
        data: Dict = None,
        params: Dict = None,
    ) -> Dict[str, Any]:
        """Make API request."""
        headers = {
            "Stripe-Version": self.api_version,
        }
        
        if method == "POST":
            headers["Idempotency-Key"] = str(uuid.uuid4())
        
        form_data = self._flatten_params(data) if data else None
        
        response = await self._client.request(
            method=method,
            url=path,
            data=form_data,
            params=params,
            headers=headers,
            raise_on_error=False,
        )
        
        result = response.json() if response.body else {}
        
        if response.status_code >= 400:
            error = result.get("error", {})
            raise StripeError(
                message=error.get("message", str(result)),
                status_code=response.status_code,
                error_type=error.get("type"),
                error_code=error.get("code"),
                param=error.get("param"),
            )
        
        return result
    
    def _flatten_params(self, params: Dict, prefix: str = "") -> Dict[str, str]:
        """Flatten nested dict for form encoding."""
        result = {}
        
        for key, value in params.items():
            full_key = f"{prefix}[{key}]" if prefix else key
            
            if value is None:
                continue
            elif isinstance(value, dict):
                result.update(self._flatten_params(value, full_key))
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, dict):
                        result.update(self._flatten_params(item, f"{full_key}[{i}]"))
                    else:
                        result[f"{full_key}[{i}]"] = str(item)
            elif isinstance(value, bool):
                result[full_key] = "true" if value else "false"
            else:
                result[full_key] = str(value)
        
        return result
    
    # =========================================================================
    # Products
    # =========================================================================
    
    async def create_product(
        self,
        name: str,
        description: str = None,
        active: bool = True,
        metadata: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        """Create a product."""
        data = {"name": name, "active": active}
        if description:
            data["description"] = description
        if metadata:
            data["metadata"] = metadata
        return await self._request("POST", "/products", data=data)
    
    async def modify_product(
        self,
        product_id: str,
        name: str = None,
        description: str = None,
        active: bool = None,
        metadata: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        """Update a product."""
        data = {}
        if name is not None:
            data["name"] = name
        if description is not None:
            data["description"] = description
        if active is not None:
            data["active"] = active
        if metadata is not None:
            data["metadata"] = metadata
        return await self._request("POST", f"/products/{product_id}", data=data)
    
    async def retrieve_product(self, product_id: str) -> Dict[str, Any]:
        """Get a product."""
        return await self._request("GET", f"/products/{product_id}")
    
    # =========================================================================
    # Prices
    # =========================================================================
    
    async def create_price(
        self,
        product: str,
        unit_amount: int,
        currency: str = "usd",
        recurring: Dict[str, Any] = None,
        nickname: str = None,
        active: bool = True,
        metadata: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        """Create a price."""
        data = {
            "product": product,
            "unit_amount": unit_amount,
            "currency": currency,
            "active": active,
        }
        if recurring:
            data["recurring"] = recurring
        if nickname:
            data["nickname"] = nickname
        if metadata:
            data["metadata"] = metadata
        return await self._request("POST", "/prices", data=data)
    
    async def modify_price(
        self,
        price_id: str,
        active: bool = None,
        nickname: str = None,
        metadata: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        """Update a price."""
        data = {}
        if active is not None:
            data["active"] = active
        if nickname is not None:
            data["nickname"] = nickname
        if metadata is not None:
            data["metadata"] = metadata
        return await self._request("POST", f"/prices/{price_id}", data=data)
    
    async def retrieve_price(self, price_id: str) -> Dict[str, Any]:
        """Get a price."""
        return await self._request("GET", f"/prices/{price_id}")
    
    # =========================================================================
    # Customers
    # =========================================================================
    
    async def create_customer(
        self,
        email: str = None,
        name: str = None,
        metadata: Dict[str, str] = None,
        payment_method: str = None,
        invoice_settings: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """Create a customer."""
        data = {}
        if email:
            data["email"] = email
        if name:
            data["name"] = name
        if metadata:
            data["metadata"] = metadata
        if payment_method:
            data["payment_method"] = payment_method
        if invoice_settings:
            data["invoice_settings"] = invoice_settings
        return await self._request("POST", "/customers", data=data)
    
    async def modify_customer(
        self,
        customer_id: str,
        email: str = None,
        name: str = None,
        metadata: Dict[str, str] = None,
        invoice_settings: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """Update a customer."""
        data = {}
        if email is not None:
            data["email"] = email
        if name is not None:
            data["name"] = name
        if metadata is not None:
            data["metadata"] = metadata
        if invoice_settings is not None:
            data["invoice_settings"] = invoice_settings
        return await self._request("POST", f"/customers/{customer_id}", data=data)
    
    async def retrieve_customer(self, customer_id: str) -> Dict[str, Any]:
        """Get a customer."""
        return await self._request("GET", f"/customers/{customer_id}")
    
    async def delete_customer(self, customer_id: str) -> Dict[str, Any]:
        """Delete a customer."""
        return await self._request("DELETE", f"/customers/{customer_id}")
    
    # =========================================================================
    # Subscriptions
    # =========================================================================
    
    async def create_subscription(
        self,
        customer: str,
        items: List[Dict[str, Any]],
        default_payment_method: str = None,
        trial_end: int = None,
        metadata: Dict[str, str] = None,
        cancel_at_period_end: bool = False,
    ) -> Dict[str, Any]:
        """Create a subscription."""
        data = {
            "customer": customer,
            "items": items,
            "cancel_at_period_end": cancel_at_period_end,
        }
        if default_payment_method:
            data["default_payment_method"] = default_payment_method
        if trial_end:
            data["trial_end"] = trial_end
        if metadata:
            data["metadata"] = metadata
        return await self._request("POST", "/subscriptions", data=data)
    
    async def modify_subscription(
        self,
        subscription_id: str,
        items: List[Dict[str, Any]] = None,
        cancel_at_period_end: bool = None,
        proration_behavior: str = None,
        metadata: Dict[str, str] = None,
    ) -> Dict[str, Any]:
        """Update a subscription."""
        data = {}
        if items is not None:
            data["items"] = items
        if cancel_at_period_end is not None:
            data["cancel_at_period_end"] = cancel_at_period_end
        if proration_behavior is not None:
            data["proration_behavior"] = proration_behavior
        if metadata is not None:
            data["metadata"] = metadata
        return await self._request("POST", f"/subscriptions/{subscription_id}", data=data)
    
    async def retrieve_subscription(
        self,
        subscription_id: str,
        expand: List[str] = None,
    ) -> Dict[str, Any]:
        """Get a subscription."""
        params = {}
        if expand:
            params["expand[]"] = expand
        return await self._request("GET", f"/subscriptions/{subscription_id}", params=params)
    
    async def cancel_subscription(
        self,
        subscription_id: str,
        immediately: bool = False,
    ) -> Dict[str, Any]:
        """Cancel a subscription."""
        if immediately:
            return await self._request("DELETE", f"/subscriptions/{subscription_id}")
        else:
            return await self.modify_subscription(subscription_id, cancel_at_period_end=True)
    
    # =========================================================================
    # Payment Methods
    # =========================================================================
    
    async def attach_payment_method(
        self,
        payment_method_id: str,
        customer: str,
    ) -> Dict[str, Any]:
        """Attach a payment method to a customer."""
        return await self._request(
            "POST",
            f"/payment_methods/{payment_method_id}/attach",
            data={"customer": customer},
        )
    
    async def detach_payment_method(self, payment_method_id: str) -> Dict[str, Any]:
        """Detach a payment method."""
        return await self._request("POST", f"/payment_methods/{payment_method_id}/detach")
    
    async def retrieve_payment_method(self, payment_method_id: str) -> Dict[str, Any]:
        """Get a payment method."""
        return await self._request("GET", f"/payment_methods/{payment_method_id}")
    
    async def list_payment_methods(
        self,
        customer: str,
        type: str = "card",
    ) -> Dict[str, Any]:
        """List customer's payment methods."""
        return await self._request(
            "GET",
            "/payment_methods",
            params={"customer": customer, "type": type},
        )
    
    # =========================================================================
    # Checkout Sessions
    # =========================================================================
    
    async def create_checkout_session(
        self,
        customer: str,
        line_items: List[Dict[str, Any]],
        mode: str,
        success_url: str,
        cancel_url: str,
        metadata: Dict[str, str] = None,
        shipping_address_collection: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """Create a Checkout Session."""
        data = {
            "customer": customer,
            "line_items": line_items,
            "mode": mode,
            "success_url": success_url,
            "cancel_url": cancel_url,
        }
        if metadata:
            data["metadata"] = metadata
        if shipping_address_collection:
            data["shipping_address_collection"] = shipping_address_collection
        return await self._request("POST", "/checkout/sessions", data=data)
    
    async def retrieve_checkout_session(
        self,
        session_id: str,
        expand: List[str] = None,
    ) -> Dict[str, Any]:
        """Get a Checkout Session."""
        params = {}
        if expand:
            for i, item in enumerate(expand):
                params[f"expand[{i}]"] = item
        return await self._request("GET", f"/checkout/sessions/{session_id}", params=params)
    
    # =========================================================================
    # Billing Portal
    # =========================================================================
    
    async def create_portal_session(
        self,
        customer: str,
        return_url: str,
    ) -> Dict[str, Any]:
        """Create a Customer Portal session."""
        return await self._request(
            "POST",
            "/billing_portal/sessions",
            data={"customer": customer, "return_url": return_url},
        )
    
    # =========================================================================
    # Invoices
    # =========================================================================
    
    async def retrieve_invoice(self, invoice_id: str) -> Dict[str, Any]:
        """Get an invoice."""
        return await self._request("GET", f"/invoices/{invoice_id}")
    
    async def pay_invoice(
        self,
        invoice_id: str,
        payment_method: str = None,
    ) -> Dict[str, Any]:
        """Pay an invoice."""
        data = {}
        if payment_method:
            data["payment_method"] = payment_method
        return await self._request("POST", f"/invoices/{invoice_id}/pay", data=data or None)
    
    async def list_invoices(
        self,
        customer: str = None,
        subscription: str = None,
        status: str = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """List invoices."""
        params = {"limit": limit}
        if customer:
            params["customer"] = customer
        if subscription:
            params["subscription"] = subscription
        if status:
            params["status"] = status
        return await self._request("GET", "/invoices", params=params)
