"""
Base Cloud Client.

Shared logic for all cloud provider clients.

Connection Pooling:
    Async: Uses http_client's connection pool (cached by base_url).
    Sync: Uses requests.Session (HTTP keep-alive).
    
    All AsyncDOClient instances share the same connection pool to api.digitalocean.com.
    Auth is passed per-request (multi-tenant safety).
    
    First request:  TCP + TLS handshake (~200-300ms)
    Subsequent:     Reuse connection (~20-50ms)
    
Configuration:
    from http_client import configure_pool_limits, PoolLimits
    
    # For high concurrency (many users, LLM streaming)
    configure_pool_limits(PoolLimits.high_concurrency())
"""

from __future__ import annotations
from typing import Dict, Any, Optional
from dataclasses import dataclass

from ..http_client import (
    HttpConfig,
    RetryConfig,
    get_pooled_client,
    close_pool,
)

from .errors import CloudError, RateLimitError, AuthenticationError, NotFoundError


@dataclass
class CloudClientConfig:
    """Configuration for cloud clients."""
    timeout: float = 30.0
    max_retries: int = 3
    retry_base_delay: float = 1.0


def _default_http_config(config: CloudClientConfig = None) -> HttpConfig:
    """Create HttpConfig with cloud-appropriate defaults."""
    config = config or CloudClientConfig()
    
    return HttpConfig(
        timeout=config.timeout,
        retry=RetryConfig(
            max_retries=config.max_retries,
            base_delay=config.retry_base_delay,
            retry_on_status={429, 500, 502, 503, 504},
        ),
        circuit_breaker=None,
    )


# =============================================================================
# Sync Client Wrapper (uses requests.Session)
# =============================================================================

class _SyncClientWrapper:
    """
    Wrapper to provide consistent interface for sync HTTP requests.
    
    Provides .request() method compatible with what cloud clients expect.
    Uses requests.Session for HTTP keep-alive.
    """
    
    def __init__(self, base_url: str, timeout: float, auth_headers: Dict[str, str] = None):
        import requests
        self._base_url = base_url
        self._timeout = timeout
        self._session = requests.Session()
        if auth_headers:
            self._session.headers.update(auth_headers)
    
    def set_bearer_token(self, token: str):
        """Update auth token."""
        self._session.headers["Authorization"] = f"Bearer {token}"
    
    def set_auth_headers(self, headers: Dict[str, str]):
        """Set custom auth headers."""
        self._session.headers.update(headers)
    
    def request(
        self,
        method: str,
        url: str,
        json: Optional[Dict] = None,
        data: Optional[Dict] = None,  # For form-encoded (Stripe)
        params: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        raise_on_error: bool = True,  # Ignored - clients handle errors
        **kwargs,
    ) -> "_SyncResponseWrapper":
        """Make HTTP request."""
        full_url = f"{self._base_url}{url}" if not url.startswith("http") else url
        
        resp = self._session.request(
            method=method,
            url=full_url,
            json=json,
            data=data,
            params=params,
            headers=headers,
            timeout=self._timeout,
        )
        
        return _SyncResponseWrapper(resp)
    
    def close(self):
        """Close the session."""
        self._session.close()


class _SyncResponseWrapper:
    """Wrapper to provide consistent response interface."""
    
    def __init__(self, response):
        self._response = response
    
    @property
    def status_code(self) -> int:
        return self._response.status_code
    
    @property
    def body(self) -> bytes:
        return self._response.content
    
    @property
    def text(self) -> str:
        return self._response.text
    
    @property
    def headers(self) -> Dict[str, str]:
        return dict(self._response.headers)
    
    def json(self) -> Any:
        return self._response.json()


# =============================================================================
# Async Client Wrapper (uses pooled httpx)
# =============================================================================

class _AsyncClientWrapper:
    """
    Wrapper around pooled httpx client.
    
    Provides .request() method compatible with what cloud clients expect.
    Adds auth header per-request for multi-tenant safety.
    """
    
    def __init__(self, httpx_client, auth_headers: Dict[str, str] = None):
        self._client = httpx_client
        self._auth_headers = auth_headers or {}
    
    def set_bearer_token(self, token: str):
        """Update auth token."""
        self._auth_headers["Authorization"] = f"Bearer {token}"
    
    def set_auth_headers(self, headers: Dict[str, str]):
        """Set custom auth headers."""
        self._auth_headers.update(headers)
    
    async def request(
        self,
        method: str,
        url: str,
        json: Optional[Dict] = None,
        data: Optional[Dict] = None,  # For form-encoded (Stripe)
        params: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        raise_on_error: bool = True,  # Ignored - clients handle errors
        **kwargs,
    ) -> "_AsyncResponseWrapper":
        """Make HTTP request."""
        req_headers = dict(self._auth_headers)
        if headers:
            req_headers.update(headers)
        
        response = await self._client.request(
            method=method,
            url=url,
            json=json,
            data=data,
            params=params,
            headers=req_headers,
        )
        
        return _AsyncResponseWrapper(response)


class _AsyncResponseWrapper:
    """Wrapper to provide consistent response interface."""
    
    def __init__(self, response):
        self._response = response
    
    @property
    def status_code(self) -> int:
        return self._response.status_code
    
    @property
    def body(self) -> bytes:
        return self._response.content
    
    @property
    def text(self) -> str:
        return self._response.text
    
    @property
    def headers(self) -> Dict[str, str]:
        return dict(self._response.headers)
    
    def json(self) -> Any:
        return self._response.json()


# =============================================================================
# Base Cloud Clients
# =============================================================================

class BaseCloudClient:
    """
    Base class for sync cloud clients.
    
    Uses requests.Session for HTTP keep-alive.
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
        
        # Provide _client for backward compatibility with child classes
        self._client = _SyncClientWrapper(
            base_url=self.BASE_URL,
            timeout=self.config.timeout,
            auth_headers=self._get_auth_headers(),
        )
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authorization headers. Override for different auth schemes."""
        return {"Authorization": f"Bearer {self.api_token}"}
    
    def close(self) -> None:
        """Close the HTTP session."""
        if hasattr(self, '_client'):
            self._client.close()
    
    def __enter__(self) -> 'BaseCloudClient':
        return self
    
    def __exit__(self, *args) -> None:
        self.close()


class AsyncBaseCloudClient:
    """
    Base class for async cloud clients.
    
    Uses connection pooling - all instances share connections to the same base_url.
    Auth is passed per-request for multi-tenant safety.
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
        self._http_config = _default_http_config(self.config)
        self._client: Optional[_AsyncClientWrapper] = None
    
    async def _ensure_client(self) -> _AsyncClientWrapper:
        """Lazily initialize pooled client wrapped with auth."""
        if self._client is None:
            httpx_client = await get_pooled_client(self.BASE_URL, self._http_config)
            self._client = _AsyncClientWrapper(
                httpx_client,
                auth_headers=self._get_auth_headers(),
            )
        return self._client
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authorization headers. Override for different auth schemes."""
        return {"Authorization": f"Bearer {self.api_token}"}
    
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
