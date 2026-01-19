"""
Base Cloud Client.

Shared logic for all cloud provider clients.

Features:
    - Connection pooling (shared per base_url)
    - Automatic retry with exponential backoff
    - Circuit breaker per service
    - Request tracing integration
    - Multi-tenant auth (per-request headers)

Connection Pooling:
    All clients share connection pools via http_client module.
    
    First request:  TCP + TLS handshake (~200-300ms)
    Subsequent:     Reuse connection (~20-50ms)
    
Configuration:
    from http_client import configure_pool_limits, PoolLimits
    
    # For high concurrency (many users, LLM streaming)
    configure_pool_limits(PoolLimits.high_concurrency())
"""

from __future__ import annotations
from typing import Dict, Any, Optional, TYPE_CHECKING
from dataclasses import dataclass

from ..http_client import (
    HttpConfig,
    RetryConfig,
    CircuitBreakerConfig,
    SyncHttpClient,
    AsyncHttpClient,
    get_pooled_sync_client,
    get_pooled_client,
    close_pool,
)

if TYPE_CHECKING:
    from ..http_client import HttpResponse

from .errors import CloudError, RateLimitError, AuthenticationError, NotFoundError


@dataclass
class CloudClientConfig:
    """Configuration for cloud clients."""
    timeout: float = 30.0
    max_retries: int = 3
    retry_base_delay: float = 1.0
    circuit_breaker_enabled: bool = True
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout: float = 60.0


def _make_http_config(config: CloudClientConfig = None) -> HttpConfig:
    """Create HttpConfig with cloud-appropriate defaults."""
    config = config or CloudClientConfig()
    
    cb_config = None
    if config.circuit_breaker_enabled:
        cb_config = CircuitBreakerConfig(
            enabled=True,
            failure_threshold=config.circuit_breaker_threshold,
            recovery_timeout=config.circuit_breaker_timeout,
        )
    
    return HttpConfig(
        timeout=config.timeout,
        retry=RetryConfig(
            max_retries=config.max_retries,
            base_delay=config.retry_base_delay,
            retry_on_status={429, 500, 502, 503, 504},
        ),
        circuit_breaker=cb_config,
    )


# =============================================================================
# Base Cloud Clients
# =============================================================================

class BaseCloudClient:
    """
    Base class for sync cloud clients.
    
    Uses pooled SyncHttpClient with retry, circuit breaker, and tracing.
    """
    
    PROVIDER = "cloud"
    BASE_URL = ""
    
    def __init__(
        self,
        api_token: str,
        config: CloudClientConfig = None,
    ):
        self.api_token = api_token
        self.config = config or CloudClientConfig()
        self._http_config = _make_http_config(self.config)
        
        # Get pooled client with full features (retry, circuit breaker, tracing)
        self._client: SyncHttpClient = get_pooled_sync_client(
            self.BASE_URL,
            self._http_config,
        )
        
        # Apply auth - deferred so subclass can override _get_auth_headers
        self._apply_auth()
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authorization headers. Override for different auth schemes."""
        return {"Authorization": f"Bearer {self.api_token}"}
    
    def _apply_auth(self) -> None:
        """Apply auth headers to client. Called after __init__ completes."""
        auth_headers = self._get_auth_headers()
        if "Authorization" in auth_headers:
            # Parse scheme and credentials from header value
            auth_value = auth_headers["Authorization"]
            if " " in auth_value:
                scheme, credentials = auth_value.split(" ", 1)
                self._client.set_auth_header(scheme, credentials)
    
    def close(self) -> None:
        """No-op. Connection pool managed globally via close_pool()."""
        pass
    
    def __enter__(self) -> 'BaseCloudClient':
        return self
    
    def __exit__(self, *args) -> None:
        pass


class AsyncBaseCloudClient:
    """
    Base class for async cloud clients.
    
    Uses pooled AsyncHttpClient with retry, circuit breaker, and tracing.
    All instances share connections to the same base_url.
    """
    
    PROVIDER = "cloud"
    BASE_URL = ""
    
    def __init__(
        self,
        api_token: str,
        config: CloudClientConfig = None,
    ):
        self.api_token = api_token
        self.config = config or CloudClientConfig()
        self._http_config = _make_http_config(self.config)
        self._client: Optional[AsyncHttpClient] = None
    
    async def _ensure_client(self) -> AsyncHttpClient:
        """Lazily initialize pooled client with auth."""
        if self._client is None:
            self._client = await get_pooled_client(
                self.BASE_URL,
                self._http_config,
            )
            # Apply auth - called here so subclass overrides are used
            self._apply_auth()
        return self._client
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authorization headers. Override for different auth schemes."""
        return {"Authorization": f"Bearer {self.api_token}"}
    
    def _apply_auth(self) -> None:
        """Apply auth headers to client."""
        if self._client is None:
            return
        auth_headers = self._get_auth_headers()
        if "Authorization" in auth_headers:
            auth_value = auth_headers["Authorization"]
            if " " in auth_value:
                scheme, credentials = auth_value.split(" ", 1)
                self._client.set_auth_header(scheme, credentials)
    
    async def close(self) -> None:
        """No-op. Connection pool managed globally via close_pool()."""
        pass
    
    async def __aenter__(self) -> 'AsyncBaseCloudClient':
        return self
    
    async def __aexit__(self, *args) -> None:
        pass


async def close_all_cloud_clients():
    """
    Close all pooled connections. Call on app shutdown.
    
    Example:
        @app.on_event("shutdown")
        async def shutdown():
            await close_all_cloud_clients()
    """
    await close_pool()
