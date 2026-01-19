"""
Base LLM client.

Shared logic for all LLM provider clients.

Uses http_client for connection pooling, retries, circuit breaker, and tracing.
"""

from __future__ import annotations
from typing import Dict, Any, Optional, AsyncIterator, TYPE_CHECKING
from dataclasses import dataclass
import json

from ...http_client import (
    HttpConfig,
    RetryConfig,
    CircuitBreakerConfig,
    SyncHttpClient,
    AsyncHttpClient,
    get_pooled_sync_client,
    get_pooled_client,
)
from .errors import (
    LLMError,
    LLMRateLimitError,
    LLMAuthError,
    LLMContextLengthError,
    LLMTimeoutError,
    LLMConnectionError,
)


@dataclass
class LLMClientConfig:
    """Configuration for LLM clients."""
    timeout: float = 120.0  # LLM calls can be slow
    max_retries: int = 3
    retry_base_delay: float = 1.0
    circuit_breaker_enabled: bool = True


def _make_llm_http_config(config: LLMClientConfig = None) -> HttpConfig:
    """Create HttpConfig with LLM-appropriate defaults."""
    config = config or LLMClientConfig()
    
    cb_config = None
    if config.circuit_breaker_enabled:
        cb_config = CircuitBreakerConfig(
            enabled=True,
            failure_threshold=5,
            recovery_timeout=60.0,
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


class BaseLLMClient:
    """
    Base class for sync LLM clients.
    
    Uses pooled SyncHttpClient with retry, circuit breaker, and tracing.
    """
    
    PROVIDER = "llm"
    BASE_URL = ""
    
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = None,
        timeout: float = 120.0,
        max_retries: int = 3,
    ):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        
        self._base_url = base_url or self.BASE_URL
        
        # Create config
        config = LLMClientConfig(
            timeout=timeout,
            max_retries=max_retries,
        )
        self._http_config = _make_llm_http_config(config)
        
        # Get pooled client
        self._client: SyncHttpClient = get_pooled_sync_client(
            self._base_url,
            self._http_config,
        )
        
        # Store auth headers for per-request use (supports non-standard auth like x-api-key)
        self._auth_headers = self._get_auth_headers()
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authorization headers. Override for different auth schemes."""
        return {"Authorization": f"Bearer {self.api_key}"}
    
    def _request(
        self,
        method: str,
        url: str,
        json: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ):
        """Make HTTP request with auth headers."""
        # Merge auth headers with request headers
        req_headers = dict(self._auth_headers)
        if headers:
            req_headers.update(headers)
        
        return self._client.request(
            method=method,
            url=url,
            json=json,
            headers=req_headers,
            raise_on_error=False,
            **kwargs,
        )
    
    def _handle_error(self, status_code: int, body: Dict[str, Any], text: str):
        """Convert HTTP errors to LLM errors."""
        error_msg = self._extract_error_message(body, text)
        
        if status_code == 401:
            raise LLMAuthError(error_msg, provider=self.PROVIDER, response_body=body)
        elif status_code == 429:
            retry_after = self._extract_retry_after(body)
            raise LLMRateLimitError(
                error_msg, provider=self.PROVIDER, retry_after=retry_after, response_body=body
            )
        elif status_code == 400:
            if "context" in error_msg.lower() or "token" in error_msg.lower():
                raise LLMContextLengthError(error_msg, provider=self.PROVIDER, response_body=body)
            raise LLMError(error_msg, provider=self.PROVIDER, status_code=status_code, response_body=body)
        else:
            raise LLMError(error_msg, provider=self.PROVIDER, status_code=status_code, response_body=body)
    
    def _extract_error_message(self, body: Dict[str, Any], text: str) -> str:
        """Extract error message from response."""
        if body:
            # OpenAI style
            if "error" in body:
                err = body["error"]
                if isinstance(err, dict):
                    return err.get("message", str(err))
                return str(err)
            # Anthropic style
            if "message" in body:
                return body["message"]
        return text[:500] if text else "Unknown error"
    
    def _extract_retry_after(self, body: Dict[str, Any]) -> Optional[float]:
        """Extract retry-after from response."""
        # Try various locations
        if body:
            if "retry_after" in body:
                return float(body["retry_after"])
            if "error" in body and isinstance(body["error"], dict):
                if "retry_after" in body["error"]:
                    return float(body["error"]["retry_after"])
        return None
    
    def close(self) -> None:
        """No-op. Connection pool managed globally."""
        pass
    
    def __enter__(self) -> "BaseLLMClient":
        return self
    
    def __exit__(self, *args) -> None:
        pass


class AsyncBaseLLMClient:
    """
    Base class for async LLM clients.
    
    Uses pooled AsyncHttpClient with retry, circuit breaker, and tracing.
    All instances share connections to the same base_url.
    """
    
    PROVIDER = "llm"
    BASE_URL = ""
    
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = None,
        timeout: float = 120.0,
        max_retries: int = 3,
    ):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        
        self._base_url = base_url or self.BASE_URL
        
        # Create config
        config = LLMClientConfig(
            timeout=timeout,
            max_retries=max_retries,
        )
        self._http_config = _make_llm_http_config(config)
        self._client: Optional[AsyncHttpClient] = None
        
        # Store auth headers for per-request use (supports non-standard auth like x-api-key)
        self._auth_headers = self._get_auth_headers()
    
    async def _ensure_client(self) -> AsyncHttpClient:
        """Lazily initialize pooled client."""
        if self._client is None:
            self._client = await get_pooled_client(
                self._base_url,
                self._http_config,
            )
        return self._client
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authorization headers. Override for different auth schemes."""
        return {"Authorization": f"Bearer {self.api_key}"}
    
    async def _request(
        self,
        method: str,
        url: str,
        json: Optional[Dict] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ):
        """Make HTTP request with auth headers."""
        client = await self._ensure_client()
        
        # Merge auth headers with request headers
        req_headers = dict(self._auth_headers)
        if headers:
            req_headers.update(headers)
        
        return await client.request(
            method=method,
            url=url,
            json=json,
            headers=req_headers,
            raise_on_error=False,
            **kwargs,
        )
    
    def _handle_error(self, status_code: int, body: Dict[str, Any], text: str):
        """Convert HTTP errors to LLM errors."""
        error_msg = self._extract_error_message(body, text)
        
        if status_code == 401:
            raise LLMAuthError(error_msg, provider=self.PROVIDER, response_body=body)
        elif status_code == 429:
            retry_after = self._extract_retry_after(body)
            raise LLMRateLimitError(
                error_msg, provider=self.PROVIDER, retry_after=retry_after, response_body=body
            )
        elif status_code == 400:
            if "context" in error_msg.lower() or "token" in error_msg.lower():
                raise LLMContextLengthError(error_msg, provider=self.PROVIDER, response_body=body)
            raise LLMError(error_msg, provider=self.PROVIDER, status_code=status_code, response_body=body)
        else:
            raise LLMError(error_msg, provider=self.PROVIDER, status_code=status_code, response_body=body)
    
    def _extract_error_message(self, body: Dict[str, Any], text: str) -> str:
        """Extract error message from response."""
        if body:
            # OpenAI style
            if "error" in body:
                err = body["error"]
                if isinstance(err, dict):
                    return err.get("message", str(err))
                return str(err)
            # Anthropic style
            if "message" in body:
                return body["message"]
        return text[:500] if text else "Unknown error"
    
    def _extract_retry_after(self, body: Dict[str, Any]) -> Optional[float]:
        """Extract retry-after from response."""
        if body:
            if "retry_after" in body:
                return float(body["retry_after"])
            if "error" in body and isinstance(body["error"], dict):
                if "retry_after" in body["error"]:
                    return float(body["error"]["retry_after"])
        return None
    
    async def close(self) -> None:
        """No-op. Connection pool managed globally."""
        pass
    
    async def __aenter__(self) -> "AsyncBaseLLMClient":
        return self
    
    async def __aexit__(self, *args) -> None:
        pass
