"""
Asynchronous HTTP client.

Uses aiohttp library for async HTTP operations.
Includes retry, circuit breaker, and tracing integration.

Usage:
    from http_client import AsyncHttpClient, HttpConfig
    
    # Basic usage
    async with AsyncHttpClient() as client:
        response = await client.get("https://api.example.com/users")
        data = response.json()
    
    # With base URL
    async with AsyncHttpClient(base_url="https://api.example.com") as client:
        response = await client.get("/users")
    
    # With custom config
    config = HttpConfig(timeout=60)
    async with AsyncHttpClient(config=config) as client:
        ...
    
    # Manual lifecycle
    client = AsyncHttpClient()
    try:
        response = await client.get("...")
    finally:
        await client.close()

Note: Requires aiohttp package: pip install aiohttp
"""

from __future__ import annotations
import time
import asyncio
from typing import Dict, Any, Optional, Union, TYPE_CHECKING

# Lazy import for aiohttp
aiohttp = None
ClientTimeout = None
TCPConnector = None

def _ensure_aiohttp():
    """Import aiohttp on first use."""
    global aiohttp, ClientTimeout, TCPConnector
    if aiohttp is None:
        try:
            import aiohttp as _aiohttp
            from aiohttp import ClientTimeout as _ClientTimeout, TCPConnector as _TCPConnector
            aiohttp = _aiohttp
            ClientTimeout = _ClientTimeout
            TCPConnector = _TCPConnector
        except ImportError:
            raise ImportError(
                "aiohttp is required for AsyncHttpClient. "
                "Install it with: pip install aiohttp"
            )

from .base import BaseHttpClient, DummySpanContext
from ..config import HttpConfig
from ..response import HttpResponse
from ..errors import (
    HttpError,
    ConnectionError as HttpConnectionError,
    TimeoutError as HttpTimeoutError,
)


class AsyncHttpClient(BaseHttpClient):
    """
    Asynchronous HTTP client with retry and circuit breaker.
    
    Built on aiohttp library.
    
    Features:
        - Automatic retries with exponential backoff
        - Circuit breaker to prevent cascade failures
        - Request/response tracing
        - Configurable timeouts
        - Connection pooling
        - Bearer token and custom auth support
    """
    
    def __init__(
        self,
        config: HttpConfig = None,
        base_url: str = None,
        circuit_breaker_name: str = None,
    ):
        super().__init__(config, base_url, circuit_breaker_name)
        self._session = None
        self._auth_header: Optional[str] = None
        self._owns_session: bool = False
    
    async def _get_session(self):
        """Get or create aiohttp session."""
        _ensure_aiohttp()  # Ensure aiohttp is available
        
        if self._session is None or self._session.closed:
            timeout = ClientTimeout(
                total=self.config.timeout,
                connect=self.config.connect_timeout,
                sock_read=self.config.get_read_timeout(),
            )
            
            connector = TCPConnector(
                ssl=self.config.verify_ssl if self.config.verify_ssl else False,
                limit=100,  # Connection pool size
                limit_per_host=20,
            )
            
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers=self.config.get_default_headers(),
            )
            self._owns_session = True
        
        return self._session
    
    def set_auth_header(self, scheme: str, credentials: str) -> None:
        """
        Set authorization header.
        
        Args:
            scheme: Auth scheme (e.g., "Bearer", "Basic")
            credentials: Auth credentials
        """
        self._auth_header = f"{scheme} {credentials}"
    
    def set_bearer_token(self, token: str) -> None:
        """Set Bearer token authorization."""
        self.set_auth_header("Bearer", token)
    
    async def request(
        self,
        method: str,
        url: str,
        params: Dict[str, Any] = None,
        data: Any = None,
        json: Any = None,
        headers: Dict[str, str] = None,
        timeout: float = None,
        raise_on_error: bool = True,
    ) -> HttpResponse:
        """
        Make async HTTP request with retry and circuit breaker.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            url: URL or path (if base_url set)
            params: Query parameters
            data: Form data or raw body
            json: JSON body (sets Content-Type)
            headers: Additional headers
            timeout: Override default timeout
            raise_on_error: Raise exception on 4xx/5xx
            
        Returns:
            HttpResponse object
            
        Raises:
            HttpError: On request failure (if raise_on_error=True)
            CircuitOpenError: If circuit breaker is open
        """
        # Check circuit breaker first
        self._check_circuit_breaker()
        
        # Build URL and headers
        full_url = self._build_url(url)
        merged_headers = self._merge_headers(headers)
        
        # Add auth header
        if self._auth_header:
            merged_headers["Authorization"] = self._auth_header
        
        # Get timeout
        request_timeout = None
        if timeout:
            _ensure_aiohttp()
            request_timeout = ClientTimeout(total=timeout)
        
        # Create span for tracing
        span_ctx = self._create_span(method, full_url, merged_headers)
        if span_ctx is None:
            span_ctx = DummySpanContext()
        
        # Retry loop
        attempt = 0
        last_exception: Optional[Exception] = None
        last_response: Optional[HttpResponse] = None
        
        with span_ctx as span:
            while True:
                self._log_request(method, full_url, attempt)
                start_time = time.perf_counter()
                
                try:
                    session = await self._get_session()
                    
                    async with session.request(
                        method=method,
                        url=full_url,
                        params=params,
                        data=data,
                        json=json,
                        headers=merged_headers,
                        timeout=request_timeout,
                        allow_redirects=self.config.follow_redirects,
                    ) as response:
                        # Read body
                        body = await response.read()
                        elapsed_ms = (time.perf_counter() - start_time) * 1000
                        
                        # Build response
                        http_response = HttpResponse(
                            status_code=response.status,
                            headers=dict(response.headers),
                            body=body,
                            url=str(response.url),
                            method=method,
                            elapsed_ms=elapsed_ms,
                            retry_count=attempt,
                        )
                        
                        self._log_response(http_response, attempt)
                        last_response = http_response
                        
                        # Check if should retry on status
                        if self._should_retry(attempt, response.status, None):
                            attempt += 1
                            retry_after = self._get_retry_after(response)
                            delay = self._calculate_retry_delay(
                                attempt - 1,
                                self.config.retry,
                                retry_after
                            )
                            await asyncio.sleep(delay)
                            continue
                        
                        # Success path
                        self._record_success()
                        self._update_span(span, response=http_response)
                        
                        if raise_on_error:
                            http_response.raise_for_status()
                        
                        return http_response
                
                except asyncio.TimeoutError:
                    elapsed_ms = (time.perf_counter() - start_time) * 1000
                    last_exception = HttpTimeoutError(
                        timeout=timeout or self.config.timeout,
                        url=full_url,
                        method=method,
                    )
                
                except Exception as e:
                    elapsed_ms = (time.perf_counter() - start_time) * 1000
                    # Handle aiohttp-specific exceptions
                    exc_name = type(e).__name__
                    
                    if exc_name in ('ClientConnectorError', 'ClientOSError'):
                        last_exception = HttpConnectionError(
                            message=str(e),
                            url=full_url,
                            method=method,
                        )
                    elif exc_name == 'ServerDisconnectedError':
                        last_exception = HttpConnectionError(
                            message=f"Server disconnected: {e}",
                            url=full_url,
                            method=method,
                        )
                    elif exc_name in ('ClientError', 'ClientResponseError'):
                        last_exception = HttpError(
                            message=str(e),
                            url=full_url,
                            method=method,
                        )
                    else:
                        # Re-raise unexpected exceptions
                        raise
                
                # Error path - check retry
                self._log_error(method, full_url, last_exception, attempt)
                
                if self._should_retry(attempt, None, last_exception):
                    attempt += 1
                    delay = self._calculate_retry_delay(attempt - 1, self.config.retry)
                    await asyncio.sleep(delay)
                    continue
                
                # No more retries
                self._record_failure()
                self._update_span(span, error=last_exception)
                raise last_exception
    
    def _get_retry_after(self, response) -> Optional[float]:
        """Extract Retry-After header from response."""
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return None
    
    # Convenience methods
    
    async def get(
        self,
        url: str,
        params: Dict[str, Any] = None,
        **kwargs,
    ) -> HttpResponse:
        """GET request."""
        return await self.request("GET", url, params=params, **kwargs)
    
    async def post(
        self,
        url: str,
        data: Any = None,
        json: Any = None,
        **kwargs,
    ) -> HttpResponse:
        """POST request."""
        return await self.request("POST", url, data=data, json=json, **kwargs)
    
    async def put(
        self,
        url: str,
        data: Any = None,
        json: Any = None,
        **kwargs,
    ) -> HttpResponse:
        """PUT request."""
        return await self.request("PUT", url, data=data, json=json, **kwargs)
    
    async def patch(
        self,
        url: str,
        data: Any = None,
        json: Any = None,
        **kwargs,
    ) -> HttpResponse:
        """PATCH request."""
        return await self.request("PATCH", url, data=data, json=json, **kwargs)
    
    async def delete(
        self,
        url: str,
        **kwargs,
    ) -> HttpResponse:
        """DELETE request."""
        return await self.request("DELETE", url, **kwargs)
    
    async def head(
        self,
        url: str,
        **kwargs,
    ) -> HttpResponse:
        """HEAD request."""
        return await self.request("HEAD", url, **kwargs)
    
    async def close(self) -> None:
        """Close the underlying session."""
        if self._session and self._owns_session:
            await self._session.close()
            self._session = None
    
    async def __aenter__(self) -> 'AsyncHttpClient':
        return self
    
    async def __aexit__(self, *args) -> None:
        await self.close()
