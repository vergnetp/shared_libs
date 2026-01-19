"""
HTTP Client Module - Unified HTTP client with resilience and tracing.

Provides sync and async HTTP clients with:
- Automatic retries with exponential backoff
- Circuit breaker to prevent cascade failures
- Request/response tracing
- Connection pooling for performance
- Response caching
- Unified error handling
- HTTP/2 support

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

Connection Pooling (RECOMMENDED for high-performance):
    from http_client import get_pooled_client
    
    # Reuses connections - much faster than creating new client each time
    client = await get_pooled_client("https://api.digitalocean.com")
    response = await client.get("/v2/droplets", headers={"Authorization": "Bearer xxx"})
    # Don't close! Pool manages lifecycle

Response Caching:
    from http_client import cached_request
    
    @cached_request(ttl=30)
    async def get_servers():
        client = await get_pooled_client("https://api.example.com")
        return await client.get("/servers")
    
    # First call: hits API
    # Subsequent calls within 30s: returns cached

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
    a tracing context is active. Spans include http_version to verify HTTP/2.
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

# Connection Pooling
from .pool import (
    get_pooled_client,
    get_pooled_sync_client,
    get_pool,
    get_async_pool,
    get_sync_pool,
    get_pool_stats,
    close_pool,
    close_sync_pool,
    configure_pool_limits,
    ConnectionPool,
    AsyncConnectionPool,
    SyncConnectionPool,
    PoolLimits,
)

# Streaming
from .clients.async_client import SSEEvent

# Response Caching
from .cache import (
    cached_request,
    get_cache,
    get_cache_stats,
    clear_cache,
    make_cache_key,
    ResponseCache,
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
    # Streaming
    "SSEEvent",
    # Connection Pooling
    "get_pooled_client",
    "get_pooled_sync_client",
    "get_pool",
    "get_async_pool",
    "get_sync_pool",
    "get_pool_stats",
    "close_pool",
    "close_sync_pool",
    "configure_pool_limits",
    "ConnectionPool",
    "AsyncConnectionPool",
    "SyncConnectionPool",
    "PoolLimits",
    # Caching
    "cached_request",
    "get_cache",
    "get_cache_stats",
    "clear_cache",
    "make_cache_key",
    "ResponseCache",
]
