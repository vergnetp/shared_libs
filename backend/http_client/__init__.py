"""
HTTP Client Module - Unified HTTP client with resilience and tracing.

Provides sync and async HTTP clients with:
- Automatic retries with exponential backoff
- Circuit breaker to prevent cascade failures
- Request/response tracing
- Unified error handling
- Configurable timeouts

Quick Start:
    # Sync client
    from http_client import SyncHttpClient
    
    client = SyncHttpClient(base_url="https://api.example.com")
    client.set_bearer_token("your-token")
    
    response = client.get("/users")
    users = response.json()
    
    # Async client
    from http_client import AsyncHttpClient
    
    async with AsyncHttpClient(base_url="https://api.example.com") as client:
        client.set_bearer_token("your-token")
        response = await client.get("/users")
        users = response.json()

Configuration:
    from http_client import HttpConfig, RetryConfig, CircuitBreakerConfig
    
    config = HttpConfig(
        timeout=60,
        retry=RetryConfig(max_retries=5, base_delay=1.0),
        circuit_breaker=CircuitBreakerConfig(failure_threshold=10),
    )
    
    client = SyncHttpClient(config=config)

Error Handling:
    from http_client import HttpError, RateLimitError, TimeoutError
    
    try:
        response = client.get("/resource")
    except RateLimitError as e:
        print(f"Rate limited, retry after {e.retry_after}s")
    except TimeoutError as e:
        print(f"Timed out after {e.timeout}s")
    except HttpError as e:
        print(f"HTTP error: {e.status_code} - {e.message}")

Tracing:
    The HTTP clients automatically create spans for each request when
    a tracing context is active. See the tracing module for details.
"""

# Configuration
from .config import (
    HttpConfig,
    RetryConfig,
    CircuitBreakerConfig,
)

# Response
from .response import HttpResponse

# Errors
from .errors import (
    HttpError,
    ConnectionError,
    TimeoutError,
    RateLimitError,
    AuthenticationError,
    AuthorizationError,
    NotFoundError,
    ValidationError,
    ServerError,
    CircuitOpenError,
    raise_for_status,
)

# Clients
from .clients import (
    SyncHttpClient,
    AsyncHttpClient,
)


__all__ = [
    # Config
    "HttpConfig",
    "RetryConfig", 
    "CircuitBreakerConfig",
    # Response
    "HttpResponse",
    # Errors
    "HttpError",
    "ConnectionError",
    "TimeoutError",
    "RateLimitError",
    "AuthenticationError",
    "AuthorizationError",
    "NotFoundError",
    "ValidationError",
    "ServerError",
    "CircuitOpenError",
    "raise_for_status",
    # Clients
    "SyncHttpClient",
    "AsyncHttpClient",
]
