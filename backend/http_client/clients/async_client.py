"""
Asynchronous HTTP client.

Uses httpx library for async HTTP operations with HTTP/2 support.
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

Note: Requires httpx package: pip install httpx
      For HTTP/2 support: pip install httpx[http2]
"""

from __future__ import annotations
import time
import asyncio
import json
from typing import Dict, Any, Optional, Union, AsyncIterator, TYPE_CHECKING
from dataclasses import dataclass

# Lazy import for httpx
httpx = None

def _ensure_httpx():
    """Import httpx on first use."""
    global httpx
    if httpx is None:
        try:
            import httpx as _httpx
            httpx = _httpx
        except ImportError:
            raise ImportError(
                "httpx is required for AsyncHttpClient. "
                "Install it with: pip install httpx"
            )

from .base import BaseHttpClient, DummySpanContext
from ..config import HttpConfig
from ..response import HttpResponse
from ..errors import (
    HttpError,
    ConnectionError as HttpConnectionError,
    TimeoutError as HttpTimeoutError,
)


@dataclass
class SSEEvent:
    """Server-Sent Event."""
    event: str = "message"
    data: str = ""
    id: Optional[str] = None
    retry: Optional[int] = None
    
    @property
    def json(self) -> Any:
        """Parse data as JSON."""
        return json.loads(self.data) if self.data else None


class AsyncHttpClient(BaseHttpClient):
    """
    Asynchronous HTTP client with retry and circuit breaker.
    
    Built on httpx library with HTTP/2 support.
    
    Features:
        - HTTP/2 multiplexing (multiple requests on single connection)
        - Connection pooling with keep-alive
        - Automatic retries with exponential backoff
        - Circuit breaker to prevent cascade failures
        - Request/response tracing
        - Configurable timeouts
        - Bearer token and custom auth support
    """
    
    def __init__(
        self,
        config: HttpConfig = None,
        base_url: str = None,
        circuit_breaker_name: str = None,
        http2: bool = False,  # Disabled by default (requires httpx[http2])
    ):
        super().__init__(config, base_url, circuit_breaker_name)
        self._client = None
        self._auth_header: Optional[str] = None
        self._owns_client: bool = True  # True = we created it, will close it
        self._http2 = http2
    
    def _inject_client(self, httpx_client) -> None:
        """
        Inject a pre-existing httpx.AsyncClient (for connection pooling).
        
        When a client is injected, we do NOT own it - the pool manages lifecycle.
        This allows multiple AsyncHttpClient instances to share a single connection.
        
        Args:
            httpx_client: An httpx.AsyncClient instance from the pool
        """
        self._client = httpx_client
        self._owns_client = False  # Pool manages lifecycle
    
    async def _get_client(self):
        """Get or create httpx async client."""
        _ensure_httpx()  # Ensure httpx is available
        
        if self._client is None or self._client.is_closed:
            timeout = httpx.Timeout(
                timeout=self.config.timeout,
                connect=self.config.connect_timeout,
                read=self.config.get_read_timeout(),
                write=30.0,
            )
            
            # Connection pool limits
            limits = httpx.Limits(
                max_keepalive_connections=20,  # Keep connections alive
                max_connections=100,           # Total pool size
                keepalive_expiry=30.0,         # Keep-alive timeout
            )
            
            self._client = httpx.AsyncClient(
                http2=self._http2,  # Enable HTTP/2!
                timeout=timeout,
                limits=limits,
                headers=self.config.get_default_headers(),
                verify=self.config.verify_ssl,
                follow_redirects=self.config.follow_redirects,
            )
            self._owns_client = True
        
        return self._client
    
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
        request_timeout = timeout if timeout else None
        
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
                    client = await self._get_client()
                    
                    response = await client.request(
                        method=method,
                        url=full_url,
                        params=params,
                        data=data,
                        json=json,
                        headers=merged_headers,
                        timeout=request_timeout,
                    )
                    
                    # Read body
                    body = response.content
                    elapsed_ms = (time.perf_counter() - start_time) * 1000
                    
                    # Get HTTP version from httpx response
                    http_version = getattr(response, 'http_version', 'HTTP/1.1') or 'HTTP/1.1'
                    
                    # Build response
                    http_response = HttpResponse(
                        status_code=response.status_code,
                        headers=dict(response.headers),
                        body=body,
                        url=str(response.url),
                        method=method,
                        elapsed_ms=elapsed_ms,
                        http_version=http_version,
                        retry_count=attempt,
                    )
                    
                    self._log_response(http_response, attempt)
                    last_response = http_response
                    
                    # Check if should retry on status
                    if self._should_retry(attempt, response.status_code, None):
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
                    # Handle httpx-specific exceptions
                    exc_name = type(e).__name__
                    
                    if exc_name in ('ConnectError', 'ConnectTimeout'):
                        last_exception = HttpConnectionError(
                            message=str(e),
                            url=full_url,
                            method=method,
                        )
                    elif exc_name == 'ReadTimeout':
                        last_exception = HttpTimeoutError(
                            timeout=timeout or self.config.timeout,
                            url=full_url,
                            method=method,
                        )
                    elif exc_name in ('RemoteProtocolError', 'LocalProtocolError'):
                        last_exception = HttpConnectionError(
                            message=f"Protocol error: {e}",
                            url=full_url,
                            method=method,
                        )
                    elif exc_name == 'HTTPStatusError':
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
    
    # =========================================================================
    # Streaming Methods
    # =========================================================================
    
    async def stream_sse(
        self,
        method: str,
        url: str,
        params: Dict[str, Any] = None,
        data: Any = None,
        json_body: Any = None,
        headers: Dict[str, str] = None,
        timeout: float = None,
    ) -> AsyncIterator[SSEEvent]:
        """
        Stream Server-Sent Events from an endpoint.
        
        Yields parsed SSE events. Does NOT retry mid-stream (would duplicate events).
        Circuit breaker still applies for the initial connection.
        
        Args:
            method: HTTP method (usually POST for LLM APIs)
            url: URL or path
            params: Query parameters
            data: Form data
            json_body: JSON body (named json_body to avoid shadowing)
            headers: Additional headers
            timeout: Connection timeout (stream can continue indefinitely)
            
        Yields:
            SSEEvent objects with event type and data
            
        Example:
            async for event in client.stream_sse("POST", "/chat", json_body={"messages": [...]}):
                if event.event == "message":
                    print(event.data)
                elif event.event == "done":
                    break
        """
        # Check circuit breaker
        self._check_circuit_breaker()
        
        # Build URL and headers
        full_url = self._build_url(url)
        merged_headers = self._merge_headers(headers)
        merged_headers["Accept"] = "text/event-stream"
        
        if self._auth_header:
            merged_headers["Authorization"] = self._auth_header
        
        # Create span for tracing (covers entire stream)
        span_ctx = self._create_span(method, full_url, merged_headers)
        if span_ctx is None:
            span_ctx = DummySpanContext()
        
        client = await self._get_client()
        
        with span_ctx as span:
            try:
                async with client.stream(
                    method=method,
                    url=full_url,
                    params=params,
                    data=data,
                    json=json_body,
                    headers=merged_headers,
                    timeout=timeout,
                ) as response:
                    # Check status before streaming
                    if response.status_code >= 400:
                        body = await response.aread()
                        self._record_failure()
                        error = HttpError(
                            status_code=response.status_code,
                            message=f"HTTP {response.status_code}",
                            response_body=body.decode("utf-8", errors="replace"),
                            url=full_url,
                            method=method,
                        )
                        self._update_span(span, error=error)
                        raise error
                    
                    # Parse SSE events
                    event = SSEEvent()
                    async for line in response.aiter_lines():
                        if not line:
                            # Empty line = end of event
                            if event.data:
                                yield event
                                event = SSEEvent()
                            continue
                        
                        if line.startswith(":"):
                            # Comment, ignore
                            continue
                        
                        if ":" in line:
                            field, _, value = line.partition(":")
                            value = value.lstrip()  # Remove leading space
                            
                            if field == "event":
                                event.event = value
                            elif field == "data":
                                if event.data:
                                    event.data += "\n" + value
                                else:
                                    event.data = value
                            elif field == "id":
                                event.id = value
                            elif field == "retry":
                                try:
                                    event.retry = int(value)
                                except ValueError:
                                    pass
                    
                    # Yield final event if any
                    if event.data:
                        yield event
                    
                    self._record_success()
                    self._update_span(span, response=HttpResponse(
                        status_code=response.status_code,
                        headers=dict(response.headers),
                        body=b"<streaming>",
                        url=full_url,
                        method=method,
                        elapsed_ms=0,
                    ))
                    
            except asyncio.TimeoutError:
                self._record_failure()
                error = HttpTimeoutError(
                    timeout=timeout or self.config.timeout,
                    url=full_url,
                    method=method,
                )
                self._update_span(span, error=error)
                raise error
            except Exception as e:
                self._record_failure()
                self._update_span(span, error=e)
                raise
    
    async def stream_ndjson(
        self,
        method: str,
        url: str,
        params: Dict[str, Any] = None,
        data: Any = None,
        json_body: Any = None,
        headers: Dict[str, str] = None,
        timeout: float = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Stream Newline-Delimited JSON (NDJSON) from an endpoint.
        
        Each line in the response is parsed as a separate JSON object.
        Used by Ollama and other APIs that stream JSON objects line-by-line.
        Does NOT retry mid-stream.
        
        Args:
            method: HTTP method
            url: URL or path
            params: Query parameters
            data: Form data
            json_body: JSON body
            headers: Additional headers
            timeout: Connection timeout (stream can continue indefinitely)
            
        Yields:
            Parsed JSON objects (dict)
            
        Example:
            async for obj in client.stream_ndjson("POST", "/api/chat", json_body=payload):
                print(obj.get("message", {}).get("content", ""))
        """
        # Check circuit breaker
        self._check_circuit_breaker()
        
        # Build URL and headers
        full_url = self._build_url(url)
        merged_headers = self._merge_headers(headers)
        
        if self._auth_header:
            merged_headers["Authorization"] = self._auth_header
        
        # Create span for tracing
        span_ctx = self._create_span(method, full_url, merged_headers)
        if span_ctx is None:
            span_ctx = DummySpanContext()
        
        client = await self._get_client()
        
        with span_ctx as span:
            try:
                async with client.stream(
                    method=method,
                    url=full_url,
                    params=params,
                    data=data,
                    json=json_body,
                    headers=merged_headers,
                    timeout=timeout,
                ) as response:
                    # Check status before streaming
                    if response.status_code >= 400:
                        body = await response.aread()
                        self._record_failure()
                        error = HttpError(
                            status_code=response.status_code,
                            message=f"HTTP {response.status_code}",
                            response_body=body.decode("utf-8", errors="replace"),
                            url=full_url,
                            method=method,
                        )
                        self._update_span(span, error=error)
                        raise error
                    
                    # Parse NDJSON lines
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            # Skip malformed lines
                            continue
                    
                    self._record_success()
                    self._update_span(span, response=HttpResponse(
                        status_code=response.status_code,
                        headers=dict(response.headers),
                        body=b"<streaming>",
                        url=full_url,
                        method=method,
                        elapsed_ms=0,
                    ))
                    
            except asyncio.TimeoutError:
                self._record_failure()
                error = HttpTimeoutError(
                    timeout=timeout or self.config.timeout,
                    url=full_url,
                    method=method,
                )
                self._update_span(span, error=error)
                raise error
            except Exception as e:
                self._record_failure()
                self._update_span(span, error=e)
                raise
    
    async def stream_raw(
        self,
        method: str,
        url: str,
        params: Dict[str, Any] = None,
        data: Any = None,
        json_body: Any = None,
        headers: Dict[str, str] = None,
        timeout: float = None,
        chunk_size: int = 8192,
    ) -> AsyncIterator[bytes]:
        """
        Stream raw bytes from an endpoint.
        
        Useful for downloading large files or binary data.
        Does NOT retry mid-stream.
        
        Args:
            method: HTTP method
            url: URL or path
            params: Query parameters
            data: Form data
            json_body: JSON body
            headers: Additional headers
            timeout: Connection timeout
            chunk_size: Size of each yielded chunk
            
        Yields:
            bytes chunks
            
        Example:
            async for chunk in client.stream_raw("GET", "/large-file"):
                file.write(chunk)
        """
        # Check circuit breaker
        self._check_circuit_breaker()
        
        # Build URL and headers
        full_url = self._build_url(url)
        merged_headers = self._merge_headers(headers)
        
        if self._auth_header:
            merged_headers["Authorization"] = self._auth_header
        
        client = await self._get_client()
        
        try:
            async with client.stream(
                method=method,
                url=full_url,
                params=params,
                data=data,
                json=json_body,
                headers=merged_headers,
                timeout=timeout,
            ) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    self._record_failure()
                    raise HttpError(
                        status_code=response.status_code,
                        message=f"HTTP {response.status_code}",
                        response_body=body.decode("utf-8", errors="replace"),
                        url=full_url,
                        method=method,
                    )
                
                async for chunk in response.aiter_bytes(chunk_size):
                    yield chunk
                
                self._record_success()
                
        except asyncio.TimeoutError:
            self._record_failure()
            raise HttpTimeoutError(
                timeout=timeout or self.config.timeout,
                url=full_url,
                method=method,
            )
    
    async def close(self) -> None:
        """Close the underlying client."""
        if self._client and self._owns_client:
            await self._client.aclose()
            self._client = None
    
    async def __aenter__(self) -> 'AsyncHttpClient':
        return self
    
    async def __aexit__(self, *args) -> None:
        await self.close()
