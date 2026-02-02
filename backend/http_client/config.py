"""
HTTP client configuration.

Centralized configuration for HTTP client behavior.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple, Set


@dataclass
class RetryConfig:
    """
    Retry behavior configuration.
    
    Attributes:
        max_retries: Maximum number of retry attempts (0 = no retries)
        base_delay: Initial delay between retries in seconds
        max_delay: Maximum delay between retries in seconds
        exponential_base: Multiplier for exponential backoff
        retry_on_status: HTTP status codes that trigger retry
        retry_on_exceptions: Exception types that trigger retry
    """
    max_retries: int = 3
    base_delay: float = 0.5
    max_delay: float = 30.0
    exponential_base: float = 2.0
    jitter: bool = True  # Add random jitter to avoid thundering herd
    
    # Status codes to retry
    retry_on_status: Set[int] = field(default_factory=lambda: {
        408,  # Request Timeout
        429,  # Too Many Requests
        500,  # Internal Server Error
        502,  # Bad Gateway
        503,  # Service Unavailable
        504,  # Gateway Timeout
    })


@dataclass
class CircuitBreakerConfig:
    """
    Circuit breaker configuration.
    
    Attributes:
        enabled: Whether to use circuit breaker
        failure_threshold: Failures before opening circuit
        recovery_timeout: Seconds to wait before half-open
        half_open_max_calls: Test calls in half-open state
        window_size: Time window for counting failures
    """
    enabled: bool = True
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_max_calls: int = 3
    window_size: float = 60.0


@dataclass
class HttpConfig:
    """
    HTTP client configuration.
    
    Usage:
        # Default config
        config = HttpConfig()
        
        # Custom config
        config = HttpConfig(
            timeout=60,
            retry=RetryConfig(max_retries=5),
            circuit_breaker=CircuitBreakerConfig(failure_threshold=10),
        )
        
        # Create client with config
        client = AsyncHttpClient(config)
    """
    # Timeouts (seconds)
    timeout: float = 30.0
    connect_timeout: float = 10.0
    read_timeout: Optional[float] = None  # Uses timeout if not set
    
    # Default headers
    headers: Dict[str, str] = field(default_factory=dict)
    
    # User agent
    user_agent: str = "shared-libs-http/1.0"
    
    # Retry config
    retry: RetryConfig = field(default_factory=RetryConfig)
    
    # Circuit breaker config
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    
    # Tracing
    tracing_enabled: bool = True
    
    # SSL verification
    verify_ssl: bool = True
    
    # Follow redirects
    follow_redirects: bool = True
    max_redirects: int = 10
    
    # Response limits
    max_response_size: int = 100 * 1024 * 1024  # 100MB
    
    def get_read_timeout(self) -> float:
        """Get read timeout (falls back to timeout)."""
        return self.read_timeout or self.timeout
    
    def get_default_headers(self) -> Dict[str, str]:
        """Get default headers including User-Agent."""
        headers = {"User-Agent": self.user_agent}
        headers.update(self.headers)
        return headers
    
    @classmethod
    def fast(cls) -> 'HttpConfig':
        """Config for fast/internal services (short timeouts)."""
        return cls(
            timeout=5.0,
            connect_timeout=2.0,
            retry=RetryConfig(max_retries=2, base_delay=0.1),
        )
    
    @classmethod
    def external_api(cls) -> 'HttpConfig':
        """Config for external APIs (longer timeouts, more retries)."""
        return cls(
            timeout=60.0,
            connect_timeout=10.0,
            retry=RetryConfig(max_retries=3, base_delay=1.0),
        )
    
    @classmethod
    def no_retry(cls) -> 'HttpConfig':
        """Config with no retries (for idempotent checks)."""
        return cls(
            retry=RetryConfig(max_retries=0),
        )

    @classmethod
    def probe(cls) -> 'HttpConfig':
        """Config for probing/polling (no retries, no circuit breaker).
        
        Use when caller has its own retry loop (e.g. waiting for a server to boot).
        """
        return cls(
            retry=RetryConfig(max_retries=0),
            circuit_breaker=CircuitBreakerConfig(enabled=False),
        )