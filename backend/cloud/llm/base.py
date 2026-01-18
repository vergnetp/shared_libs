"""
Base LLM client.

Shared logic for all LLM provider clients.

Uses http_client for connection pooling, retries, and tracing.
"""

from __future__ import annotations
from typing import Dict, Any, Optional, AsyncIterator
from dataclasses import dataclass
import json

from ..base import _SyncClientWrapper, _AsyncClientWrapper
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


class BaseLLMClient:
    """
    Base class for sync LLM clients.
    
    Uses requests.Session for HTTP keep-alive.
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
        self._client = _SyncClientWrapper(
            base_url=self._base_url,
            timeout=timeout,
            auth_headers=self._get_auth_headers(),
        )
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authorization headers. Override for different auth schemes."""
        return {"Authorization": f"Bearer {self.api_key}"}
    
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
        """Close the HTTP session."""
        if hasattr(self, '_client'):
            self._client.close()
    
    def __enter__(self) -> "BaseLLMClient":
        return self
    
    def __exit__(self, *args) -> None:
        self.close()


class AsyncBaseLLMClient:
    """
    Base class for async LLM clients.
    
    Uses connection pooling - all instances share connections to the same base_url.
    Auth is passed per-request for multi-tenant safety.
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
        self._client: Optional[_AsyncClientWrapper] = None
    
    async def _ensure_client(self) -> _AsyncClientWrapper:
        """Lazily initialize pooled client wrapped with auth."""
        if self._client is None:
            from ...http_client import get_pooled_client, HttpConfig, RetryConfig
            
            config = HttpConfig(
                timeout=self.timeout,
                retry=RetryConfig(
                    max_retries=self.max_retries,
                    base_delay=1.0,
                    retry_on_status={429, 500, 502, 503, 504},
                ),
                circuit_breaker=None,
            )
            
            httpx_client = await get_pooled_client(self._base_url, config)
            self._client = _AsyncClientWrapper(
                httpx_client,
                auth_headers=self._get_auth_headers(),
            )
        return self._client
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authorization headers. Override for different auth schemes."""
        return {"Authorization": f"Bearer {self.api_key}"}
    
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
        """No-op. Connection pool managed globally via close_pool()."""
        pass
    
    async def __aenter__(self) -> "AsyncBaseLLMClient":
        return self
    
    async def __aexit__(self, *args) -> None:
        pass


async def _stream_sse(response) -> AsyncIterator[Dict[str, Any]]:
    """
    Parse Server-Sent Events from streaming response.
    
    Yields parsed JSON data from each 'data:' line.
    """
    async for line in response.aiter_lines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("data:"):
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                continue
