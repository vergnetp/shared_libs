"""
Base Cloud Client.

Shared logic for all cloud provider clients.
"""

from __future__ import annotations
from typing import Dict, Any, Optional
from dataclasses import dataclass

from ..http_client import (
    SyncHttpClient,
    AsyncHttpClient,
    HttpConfig,
    RetryConfig,
    CircuitBreakerConfig,
    HttpError,
    HttpResponse,
)

from .errors import CloudError, RateLimitError, AuthenticationError, NotFoundError


@dataclass
class CloudClientConfig:
    """Configuration for cloud clients."""
    timeout: float = 30.0
    max_retries: int = 3
    retry_base_delay: float = 1.0
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout: float = 60.0


def default_http_config(
    config: CloudClientConfig = None,
    circuit_breaker_name: str = None,
) -> HttpConfig:
    """Create HttpConfig with cloud-appropriate defaults."""
    config = config or CloudClientConfig()
    
    return HttpConfig(
        timeout=config.timeout,
        retry=RetryConfig(
            max_retries=config.max_retries,
            base_delay=config.retry_base_delay,
            retry_on_status={429, 500, 502, 503, 504},
        ),
        circuit_breaker=CircuitBreakerConfig(
            failure_threshold=config.circuit_breaker_threshold,
            recovery_timeout=config.circuit_breaker_timeout,
        ) if circuit_breaker_name else None,
    )


class BaseCloudClient:
    """Base class for sync cloud clients."""
    
    PROVIDER = "cloud"
    BASE_URL = ""
    
    def __init__(
        self,
        api_token: str,
        config: CloudClientConfig = None,
    ):
        self.api_token = api_token
        self.config = config or CloudClientConfig()
        
        http_config = default_http_config(
            self.config,
            circuit_breaker_name=f"{self.PROVIDER}-api",
        )
        
        self._client = SyncHttpClient(
            config=http_config,
            base_url=self.BASE_URL,
            circuit_breaker_name=f"{self.PROVIDER}-api",
        )
        self._client.set_bearer_token(api_token)
    
    def _handle_error(self, response: HttpResponse, provider: str = None) -> None:
        """Convert HTTP errors to cloud-specific errors."""
        provider = provider or self.PROVIDER
        
        if response.status_code == 401:
            raise AuthenticationError(provider=provider)
        elif response.status_code == 404:
            raise NotFoundError("Resource not found", provider=provider)
        elif response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise RateLimitError(
                retry_after=float(retry_after) if retry_after else None,
                provider=provider,
            )
        elif response.status_code >= 400:
            body = response.json() if response.body else {}
            message = body.get("message") or body.get("error") or f"HTTP {response.status_code}"
            raise CloudError(
                message=message,
                status_code=response.status_code,
                provider=provider,
                response=body,
            )
    
    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()
    
    def __enter__(self) -> 'BaseCloudClient':
        return self
    
    def __exit__(self, *args) -> None:
        self.close()


# Global pool of async HTTP clients for connection reuse
_async_http_client_pool: Dict[str, AsyncHttpClient] = {}


def _get_pool_key(base_url: str, token: str) -> str:
    """Generate cache key for HTTP client pool."""
    import hashlib
    token_hash = hashlib.sha256(token.encode()).hexdigest()[:16]
    return f"{base_url}:{token_hash}"


class AsyncBaseCloudClient:
    """
    Base class for async cloud clients.
    
    Uses connection pooling - HTTP clients are cached per (base_url, token).
    This means subsequent requests reuse TCP connections (much faster).
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
        
        # Use pooled HTTP client for connection reuse
        pool_key = _get_pool_key(self.BASE_URL, api_token)
        
        if pool_key in _async_http_client_pool:
            # Reuse existing client (warm connection)
            self._client = _async_http_client_pool[pool_key]
            self._owns_client = False
        else:
            # Create new client and add to pool
            http_config = default_http_config(
                self.config,
                circuit_breaker_name=f"{self.PROVIDER}-api-async",
            )
            
            self._client = AsyncHttpClient(
                config=http_config,
                base_url=self.BASE_URL,
                circuit_breaker_name=f"{self.PROVIDER}-api-async",
            )
            self._client.set_bearer_token(api_token)
            _async_http_client_pool[pool_key] = self._client
            self._owns_client = True
    
    def _handle_error(self, response: HttpResponse, provider: str = None) -> None:
        """Convert HTTP errors to cloud-specific errors."""
        provider = provider or self.PROVIDER
        
        if response.status_code == 401:
            raise AuthenticationError(provider=provider)
        elif response.status_code == 404:
            raise NotFoundError("Resource not found", provider=provider)
        elif response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise RateLimitError(
                retry_after=float(retry_after) if retry_after else None,
                provider=provider,
            )
        elif response.status_code >= 400:
            body = response.json() if response.body else {}
            message = body.get("message") or body.get("error") or f"HTTP {response.status_code}"
            raise CloudError(
                message=message,
                status_code=response.status_code,
                provider=provider,
                response=body,
            )
    
    async def close(self) -> None:
        """No-op: pooled clients are managed globally."""
        # Don't close - connection pool manages lifecycle
        pass
    
    async def __aenter__(self) -> 'AsyncBaseCloudClient':
        return self
    
    async def __aexit__(self, *args) -> None:
        # Don't close - pool manages lifecycle
        pass
