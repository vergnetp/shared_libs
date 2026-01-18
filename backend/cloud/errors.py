"""
Cloud Provider Errors.

Unified error hierarchy for all cloud providers.
"""

from typing import Dict, Any, List, Optional


class CloudError(Exception):
    """Base error for all cloud provider operations."""
    
    def __init__(
        self,
        message: str,
        status_code: int = 0,
        provider: str = None,
        response: Dict[str, Any] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.provider = provider
        self.response = response or {}
    
    def __str__(self) -> str:
        if self.status_code:
            return f"[{self.provider or 'Cloud'}] {self.status_code}: {self.message}"
        return f"[{self.provider or 'Cloud'}] {self.message}"


class DOError(CloudError):
    """DigitalOcean API error."""
    
    def __init__(
        self,
        message: str,
        status_code: int = 0,
        response: Dict[str, Any] = None,
    ):
        super().__init__(
            message=message,
            status_code=status_code,
            provider="DigitalOcean",
            response=response,
        )


class CloudflareError(CloudError):
    """Cloudflare API error."""
    
    def __init__(
        self,
        message: str,
        errors: List[Dict[str, Any]] = None,
        status_code: int = 0,
    ):
        super().__init__(
            message=message,
            status_code=status_code,
            provider="Cloudflare",
            response={"errors": errors or []},
        )
        self.errors = errors or []


class RateLimitError(CloudError):
    """Rate limit exceeded."""
    
    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: float = None,
        provider: str = None,
    ):
        super().__init__(
            message=message,
            status_code=429,
            provider=provider,
        )
        self.retry_after = retry_after


class AuthenticationError(CloudError):
    """Authentication failed (invalid token)."""
    
    def __init__(self, message: str = "Authentication failed", provider: str = None):
        super().__init__(
            message=message,
            status_code=401,
            provider=provider,
        )


class NotFoundError(CloudError):
    """Resource not found."""
    
    def __init__(self, message: str, provider: str = None):
        super().__init__(
            message=message,
            status_code=404,
            provider=provider,
        )


class StripeError(CloudError):
    """Stripe API error."""
    
    def __init__(
        self,
        message: str,
        status_code: int = 0,
        error_type: str = None,
        error_code: str = None,
        param: str = None,
    ):
        super().__init__(
            message=message,
            status_code=status_code,
            provider="Stripe",
        )
        self.error_type = error_type
        self.error_code = error_code
        self.param = param
