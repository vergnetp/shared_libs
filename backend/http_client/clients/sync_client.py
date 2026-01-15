"""
Synchronous HTTP client.

Uses requests library for sync HTTP operations.
Includes retry, circuit breaker, and tracing integration.

Usage:
    from http import SyncHttpClient, HttpConfig
    
    # Basic usage
    client = SyncHttpClient()
    response = client.get("https://api.example.com/users")
    data = response.json()
    
    # With base URL
    client = SyncHttpClient(base_url="https://api.example.com")
    response = client.get("/users")
    
    # With custom config
    config = HttpConfig(timeout=60, retry=RetryConfig(max_retries=5))
    client = SyncHttpClient(config=config)
    
    # With auth
    client = SyncHttpClient(base_url="https://api.example.com")
    client.set_auth_header("Bearer", "token123")
"""

from __future__ import annotations
import time
from typing import Dict, Any, Optional, Union

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .base import BaseHttpClient, DummySpanContext
from ..config import HttpConfig
from ..response import HttpResponse
from ..errors import (
    HttpError,
    ConnectionError as HttpConnectionError,
    TimeoutError as HttpTimeoutError,
    raise_for_status,
)


class SyncHttpClient(BaseHttpClient):
    """
    Synchronous HTTP client with retry and circuit breaker.
    
    Built on requests library.
    
    Features:
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
    ):
        super().__init__(config, base_url, circuit_breaker_name)
        self._session: Optional[requests.Session] = None
        self._auth_header: Optional[str] = None
    
    def _get_session(self) -> requests.Session:
        """Get or create requests session."""
        if self._session is None:
            self._session = requests.Session()
            
            # Configure default headers
            self._session.headers.update(self.config.get_default_headers())
            
            # Configure SSL verification
            self._session.verify = self.config.verify_ssl
        
        return self._session
    
    def set_auth_header(self, scheme: str, credentials: str) -> None:
        """
        Set authorization header.
        
        Args:
            scheme: Auth scheme (e.g., "Bearer", "Basic")
            credentials: Auth credentials (e.g., token, encoded credentials)
        """
        self._auth_header = f"{scheme} {credentials}"
    
    def set_bearer_token(self, token: str) -> None:
        """Set Bearer token authorization."""
        self.set_auth_header("Bearer", token)
    
    def request(
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
        Make HTTP request with retry and circuit breaker.
        
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
        request_timeout = timeout or self.config.timeout
        
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
                    response = self._get_session().request(
                        method=method,
                        url=full_url,
                        params=params,
                        data=data,
                        json=json,
                        headers=merged_headers,
                        timeout=request_timeout,
                        allow_redirects=self.config.follow_redirects,
                    )
                    
                    elapsed_ms = (time.perf_counter() - start_time) * 1000
                    
                    # Build response
                    http_response = HttpResponse(
                        status_code=response.status_code,
                        headers=dict(response.headers),
                        body=response.content,
                        url=str(response.url),
                        method=method,
                        elapsed_ms=elapsed_ms,
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
                        time.sleep(delay)
                        continue
                    
                    # Success path
                    self._record_success()
                    self._update_span(span, response=http_response)
                    
                    if raise_on_error:
                        http_response.raise_for_status()
                    
                    return http_response
                    
                except requests.exceptions.Timeout as e:
                    elapsed_ms = (time.perf_counter() - start_time) * 1000
                    last_exception = HttpTimeoutError(
                        timeout=request_timeout,
                        url=full_url,
                        method=method,
                    )
                    
                except requests.exceptions.ConnectionError as e:
                    elapsed_ms = (time.perf_counter() - start_time) * 1000
                    last_exception = HttpConnectionError(
                        message=str(e),
                        url=full_url,
                        method=method,
                    )
                    
                except requests.exceptions.RequestException as e:
                    elapsed_ms = (time.perf_counter() - start_time) * 1000
                    last_exception = HttpError(
                        message=str(e),
                        url=full_url,
                        method=method,
                    )
                
                # Error path - check retry
                self._log_error(method, full_url, last_exception, attempt)
                
                if self._should_retry(attempt, None, last_exception):
                    attempt += 1
                    delay = self._calculate_retry_delay(attempt - 1, self.config.retry)
                    time.sleep(delay)
                    continue
                
                # No more retries
                self._record_failure()
                self._update_span(span, error=last_exception)
                raise last_exception
    
    def _get_retry_after(self, response: requests.Response) -> Optional[float]:
        """Extract Retry-After header from response."""
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return None
    
    # Convenience methods
    
    def get(
        self,
        url: str,
        params: Dict[str, Any] = None,
        **kwargs,
    ) -> HttpResponse:
        """GET request."""
        return self.request("GET", url, params=params, **kwargs)
    
    def post(
        self,
        url: str,
        data: Any = None,
        json: Any = None,
        **kwargs,
    ) -> HttpResponse:
        """POST request."""
        return self.request("POST", url, data=data, json=json, **kwargs)
    
    def put(
        self,
        url: str,
        data: Any = None,
        json: Any = None,
        **kwargs,
    ) -> HttpResponse:
        """PUT request."""
        return self.request("PUT", url, data=data, json=json, **kwargs)
    
    def patch(
        self,
        url: str,
        data: Any = None,
        json: Any = None,
        **kwargs,
    ) -> HttpResponse:
        """PATCH request."""
        return self.request("PATCH", url, data=data, json=json, **kwargs)
    
    def delete(
        self,
        url: str,
        **kwargs,
    ) -> HttpResponse:
        """DELETE request."""
        return self.request("DELETE", url, **kwargs)
    
    def head(
        self,
        url: str,
        **kwargs,
    ) -> HttpResponse:
        """HEAD request."""
        return self.request("HEAD", url, **kwargs)
    
    def close(self) -> None:
        """Close the underlying session."""
        if self._session:
            self._session.close()
            self._session = None
    
    def __enter__(self) -> 'SyncHttpClient':
        return self
    
    def __exit__(self, *args) -> None:
        self.close()
