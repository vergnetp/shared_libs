"""
Base HTTP client with shared logic.

Contains common functionality used by both sync and async clients:
- Request/response logging
- Retry logic calculation
- Circuit breaker integration
- Tracing integration
"""

from __future__ import annotations
import time
import random
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import HttpConfig, RetryConfig
    from ..response import HttpResponse

logger = logging.getLogger(__name__)


class BaseHttpClient(ABC):
    """
    Base class for HTTP clients.
    
    Provides:
    - Retry delay calculation with exponential backoff
    - Circuit breaker integration
    - Tracing span creation
    - Request/response logging
    """
    
    def __init__(
        self,
        config: 'HttpConfig' = None,
        base_url: str = None,
        circuit_breaker_name: str = None,
    ):
        """
        Initialize base client.
        
        Args:
            config: HTTP configuration (uses defaults if not provided)
            base_url: Base URL to prepend to all requests
            circuit_breaker_name: Name for circuit breaker (uses base_url if not provided)
        """
        from ..config import HttpConfig
        
        self.config = config or HttpConfig()
        self.base_url = base_url.rstrip('/') if base_url else None
        self._circuit_breaker_name = circuit_breaker_name or base_url or "http_client"
        self._circuit_breaker = None
    
    def _get_circuit_breaker(self):
        """Get or create circuit breaker instance."""
        if not self.config.circuit_breaker.enabled:
            return None
        
        if self._circuit_breaker is None:
            try:
                from ...resilience import CircuitBreaker
                cb_config = self.config.circuit_breaker
                self._circuit_breaker = CircuitBreaker.get_or_create(
                    name=self._circuit_breaker_name,
                    failure_threshold=cb_config.failure_threshold,
                    recovery_timeout=cb_config.recovery_timeout,
                    half_open_max_calls=cb_config.half_open_max_calls,
                    window_size=cb_config.window_size,
                )
            except ImportError:
                logger.debug("resilience module not available, circuit breaker disabled")
                self.config.circuit_breaker.enabled = False
                return None
        
        return self._circuit_breaker
    
    def _check_circuit_breaker(self) -> None:
        """
        Check if circuit breaker allows request.
        
        Raises:
            CircuitOpenError: If circuit is open
        """
        cb = self._get_circuit_breaker()
        if cb and not cb.allow_request():
            from ..errors import CircuitOpenError
            raise CircuitOpenError(service=self._circuit_breaker_name)
    
    def _record_success(self) -> None:
        """Record successful request to circuit breaker."""
        cb = self._get_circuit_breaker()
        if cb:
            cb.record_success()
    
    def _record_failure(self) -> None:
        """Record failed request to circuit breaker."""
        cb = self._get_circuit_breaker()
        if cb:
            cb.record_failure()
    
    def _build_url(self, url: str) -> str:
        """Build full URL from base_url and path."""
        if url.startswith(('http://', 'https://')):
            return url
        if self.base_url:
            return f"{self.base_url}/{url.lstrip('/')}"
        return url
    
    def _merge_headers(self, headers: Optional[Dict[str, str]]) -> Dict[str, str]:
        """Merge request headers with defaults."""
        merged = self.config.get_default_headers()
        if headers:
            merged.update(headers)
        return merged
    
    def _calculate_retry_delay(
        self,
        attempt: int,
        retry_config: 'RetryConfig',
        retry_after: Optional[float] = None,
    ) -> float:
        """
        Calculate delay before next retry.
        
        Uses exponential backoff with optional jitter.
        Respects Retry-After header if provided.
        
        Args:
            attempt: Current attempt number (0-based)
            retry_config: Retry configuration
            retry_after: Retry-After header value (overrides calculation)
        """
        if retry_after is not None:
            return min(retry_after, retry_config.max_delay)
        
        # Exponential backoff
        delay = retry_config.base_delay * (retry_config.exponential_base ** attempt)
        delay = min(delay, retry_config.max_delay)
        
        # Add jitter
        if retry_config.jitter:
            delay = delay * random.uniform(0.8, 1.2)
        
        return delay
    
    def _should_retry(
        self,
        attempt: int,
        status_code: Optional[int],
        exception: Optional[Exception],
    ) -> bool:
        """
        Determine if request should be retried.
        
        Args:
            attempt: Current attempt number (0-based)
            status_code: HTTP status code (if response received)
            exception: Exception raised (if any)
        """
        retry_config = self.config.retry
        
        # Check max retries
        if attempt >= retry_config.max_retries:
            return False
        
        # Retry on connection errors
        if exception is not None:
            from ..errors import ConnectionError, TimeoutError
            if isinstance(exception, (ConnectionError, TimeoutError)):
                return True
            # Also retry on underlying library errors
            return self._is_retryable_exception(exception)
        
        # Retry on specific status codes
        if status_code and status_code in retry_config.retry_on_status:
            return True
        
        return False
    
    def _is_retryable_exception(self, exception: Exception) -> bool:
        """Check if exception type is retryable."""
        # Connection errors from various libraries
        retryable_types = (
            "ConnectionError",
            "ConnectTimeout",
            "ReadTimeout",
            "ConnectionResetError",
            "ConnectionRefusedError",
            "TimeoutError",
            "ClientConnectorError",  # aiohttp
            "ServerDisconnectedError",  # aiohttp
        )
        return type(exception).__name__ in retryable_types
    
    def _create_span(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
    ):
        """
        Create tracing span for HTTP request.
        
        Returns trace_span context manager or DummySpanContext if tracing unavailable.
        """
        if not self.config.tracing_enabled:
            return None
        
        try:
            from tracing import trace_span
            
            return trace_span(
                f"http.{method}",
                http_method=method,
                http_url=url,
            )
        except ImportError:
            return None
    
    def _update_span(
        self,
        span,
        response: Optional['HttpResponse'] = None,
        error: Optional[Exception] = None,
    ) -> None:
        """Update span with response or error information."""
        if span is None:
            return
        
        try:
            if response:
                span.metadata["http_status_code"] = response.status_code
                span.metadata["http_response_body_size"] = len(response.body)
                span.metadata["http_version"] = response.http_version
                span.metadata["elapsed_ms"] = response.elapsed_ms
                if response.status_code >= 400:
                    span.status = "error"
            
            if error:
                span.status = "error"
                span.error = str(error)
                span.error_type = type(error).__name__
        except Exception:
            pass  # Don't let tracing errors break the request
    
    def _log_request(
        self,
        method: str,
        url: str,
        attempt: int = 0,
    ) -> None:
        """Log outgoing request."""
        if attempt > 0:
            logger.debug(f"HTTP {method} {url} (retry {attempt})")
        else:
            logger.debug(f"HTTP {method} {url}")
    
    def _log_response(
        self,
        response: 'HttpResponse',
        attempt: int = 0,
    ) -> None:
        """Log response."""
        level = logging.WARNING if response.status_code >= 400 else logging.DEBUG
        logger.log(
            level,
            f"HTTP {response.method} {response.url} -> {response.status_code} "
            f"({response.elapsed_ms:.0f}ms, {len(response.body)} bytes)"
            + (f" [retry {attempt}]" if attempt > 0 else "")
        )
    
    def _log_error(
        self,
        method: str,
        url: str,
        error: Exception,
        attempt: int = 0,
    ) -> None:
        """Log error."""
        logger.warning(
            f"HTTP {method} {url} failed: {type(error).__name__}: {error}"
            + (f" [retry {attempt}]" if attempt > 0 else "")
        )


class DummySpanContext:
    """Dummy context manager when tracing is disabled."""
    
    def __init__(self):
        self.span = type('DummySpan', (), {
            'metadata': {},
            'status': 'ok',
            'error': None,
            'error_type': None,
        })()
    
    def __enter__(self):
        return self.span
    
    def __exit__(self, *args):
        pass