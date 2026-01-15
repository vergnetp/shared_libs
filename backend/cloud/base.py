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


class AsyncBaseCloudClient:
    """Base class for async cloud clients."""
    
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
            circuit_breaker_name=f"{self.PROVIDER}-api-async",
        )
        
        self._client = AsyncHttpClient(
            config=http_config,
            base_url=self.BASE_URL,
            circuit_breaker_name=f"{self.PROVIDER}-api-async",
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
    
    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.close()
    
    async def __aenter__(self) -> 'AsyncBaseCloudClient':
        return self
    
    async def __aexit__(self, *args) -> None:
        await self.close()
