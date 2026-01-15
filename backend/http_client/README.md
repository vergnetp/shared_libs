# HTTP Module

Unified HTTP client with resilience and tracing.

## Overview

The HTTP module provides sync and async HTTP clients with:
- Automatic retries with exponential backoff
- Circuit breaker to prevent cascade failures
- Request/response tracing integration
- Unified error handling
- Configurable timeouts

## Quick Start

### Synchronous Client

```python
from http import SyncHttpClient

# Basic usage
client = SyncHttpClient(base_url="https://api.example.com")
client.set_bearer_token("your-token")

response = client.get("/users")
users = response.json()

# With context manager
with SyncHttpClient(base_url="https://api.example.com") as client:
    response = client.post("/users", json={"name": "John"})
    user = response.json()
```

### Asynchronous Client

```python
from http import AsyncHttpClient

async with AsyncHttpClient(base_url="https://api.example.com") as client:
    client.set_bearer_token("your-token")
    
    response = await client.get("/users")
    users = response.json()
    
    # Multiple concurrent requests
    import asyncio
    responses = await asyncio.gather(
        client.get("/users/1"),
        client.get("/users/2"),
        client.get("/users/3"),
    )
```

## Configuration

### HttpConfig

```python
from http import HttpConfig, RetryConfig, CircuitBreakerConfig

config = HttpConfig(
    # Timeouts
    timeout=60.0,
    connect_timeout=10.0,
    read_timeout=30.0,
    
    # Default headers
    headers={"X-Custom": "value"},
    
    # Retry behavior
    retry=RetryConfig(
        max_retries=5,
        base_delay=1.0,
        max_delay=30.0,
        exponential_base=2.0,
        jitter=True,
        retry_on_status={429, 500, 502, 503, 504},
    ),
    
    # Circuit breaker
    circuit_breaker=CircuitBreakerConfig(
        enabled=True,
        failure_threshold=5,
        recovery_timeout=30.0,
        half_open_max_calls=3,
        window_size=60.0,
    ),
    
    # Other
    tracing_enabled=True,
    verify_ssl=True,
    follow_redirects=True,
)

client = SyncHttpClient(config=config)
```

### Preset Configurations

```python
# Fast internal services
config = HttpConfig.fast()  # 5s timeout, 2 retries

# External APIs
config = HttpConfig.external_api()  # 60s timeout, 3 retries

# No retry (for idempotent checks)
config = HttpConfig.no_retry()
```

## Error Handling

### Error Hierarchy

```
HttpError (base)
├── ConnectionError     # Failed to connect
├── TimeoutError        # Request timed out
├── RateLimitError      # 429 Too Many Requests
├── AuthenticationError # 401 Unauthorized
├── AuthorizationError  # 403 Forbidden
├── NotFoundError       # 404 Not Found
├── ValidationError     # 400/422 Bad Request
├── ServerError         # 5xx Server Error
└── CircuitOpenError    # Circuit breaker open
```

### Handling Errors

```python
from http import (
    HttpError, 
    RateLimitError, 
    TimeoutError,
    CircuitOpenError,
)

try:
    response = client.get("/resource")
except RateLimitError as e:
    # Wait and retry
    print(f"Rate limited, retry after {e.retry_after}s")
except TimeoutError as e:
    print(f"Timed out after {e.timeout}s")
except CircuitOpenError as e:
    print(f"Circuit open for {e.service}")
except HttpError as e:
    print(f"HTTP {e.status_code}: {e.message}")
    print(f"URL: {e.url}")
    print(f"Response: {e.response_body}")
```

### Manual Status Check

```python
# Don't raise on error
response = client.get("/resource", raise_on_error=False)

if not response.ok:
    print(f"Error: {response.status_code}")
    print(response.text)
else:
    data = response.json()
```

## Response Object

```python
response = client.get("/users")

# Status
response.status_code  # 200
response.ok           # True (2xx)
response.is_redirect  # False (3xx)
response.is_client_error  # False (4xx)
response.is_server_error  # False (5xx)

# Body
response.body   # bytes
response.text   # str
response.json() # parsed JSON

# Headers
response.headers  # Dict[str, str]
response.header("Content-Type")  # Case-insensitive
response.content_type
response.content_length

# Metadata
response.url          # Final URL (after redirects)
response.method       # HTTP method
response.elapsed_ms   # Request duration
response.retry_count  # Number of retries
```

## Circuit Breaker

The circuit breaker prevents cascade failures by temporarily blocking requests to failing services.

### States

1. **CLOSED** (normal): Requests go through
2. **OPEN** (failing): Requests immediately rejected
3. **HALF-OPEN** (testing): Limited requests allowed to test recovery

### Configuration

```python
config = HttpConfig(
    circuit_breaker=CircuitBreakerConfig(
        enabled=True,
        failure_threshold=5,     # Open after 5 failures
        recovery_timeout=30.0,   # Wait 30s before testing
        half_open_max_calls=3,   # Allow 3 test requests
        window_size=60.0,        # Count failures in 60s window
    )
)
```

### Per-Service Breakers

Each `base_url` gets its own circuit breaker:

```python
# Separate circuit breakers
stripe = AsyncHttpClient(base_url="https://api.stripe.com")
twilio = AsyncHttpClient(base_url="https://api.twilio.com")

# Custom name
client = AsyncHttpClient(
    base_url="https://api.example.com",
    circuit_breaker_name="example-api",
)
```

## Retry Behavior

### What Gets Retried

By default, these are retried:
- Connection errors
- Timeouts
- Status codes: 408, 429, 500, 502, 503, 504

### Exponential Backoff

```
Attempt 1: immediate
Attempt 2: 0.5s × 2^0 = 0.5s (± jitter)
Attempt 3: 0.5s × 2^1 = 1.0s (± jitter)
Attempt 4: 0.5s × 2^2 = 2.0s (± jitter)
...up to max_delay
```

### Retry-After Header

The client respects the `Retry-After` header for 429 responses.

## Tracing Integration

When the `tracing` module is active, HTTP requests automatically create spans:

```python
from tracing import RequestContext, set_context

ctx = RequestContext.create()
set_context(ctx)

# This creates a span: "HTTP GET"
response = await client.get("/users")

# View traces
for span in ctx.get_spans():
    print(f"{span.name}: {span.duration_ms}ms")
```

Span attributes include:
- `http_method`: GET, POST, etc.
- `http_url`: Full URL
- `http_status_code`: Response status
- `http_response_body_size`: Response size
- `elapsed_ms`: Duration

## Authentication

```python
# Bearer token
client.set_bearer_token("token123")

# Custom auth
client.set_auth_header("Basic", "dXNlcjpwYXNz")

# In headers
client.get("/resource", headers={"Authorization": "Bearer token"})
```

---

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `SyncHttpClient`

Synchronous HTTP client with retry and circuit breaker.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `config: HttpConfig=None`, `base_url: str=None`, `circuit_breaker_name: str=None` | | Initialization | Initialize sync client |
| | `set_auth_header` | `scheme: str`, `credentials: str` | `None` | Auth | Set authorization header |
| | `set_bearer_token` | `token: str` | `None` | Auth | Set Bearer token |
| | `request` | `method: str`, `url: str`, `params: Dict=None`, `data: Any=None`, `json: Any=None`, `headers: Dict=None`, `timeout: float=None`, `raise_on_error: bool=True` | `HttpResponse` | Request | Make HTTP request |
| | `get` | `url: str`, `params: Dict=None`, `**kwargs` | `HttpResponse` | Request | GET request |
| | `post` | `url: str`, `data: Any=None`, `json: Any=None`, `**kwargs` | `HttpResponse` | Request | POST request |
| | `put` | `url: str`, `data: Any=None`, `json: Any=None`, `**kwargs` | `HttpResponse` | Request | PUT request |
| | `patch` | `url: str`, `data: Any=None`, `json: Any=None`, `**kwargs` | `HttpResponse` | Request | PATCH request |
| | `delete` | `url: str`, `**kwargs` | `HttpResponse` | Request | DELETE request |
| | `head` | `url: str`, `**kwargs` | `HttpResponse` | Request | HEAD request |
| | `close` | | `None` | Lifecycle | Close underlying session |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `AsyncHttpClient`

Asynchronous HTTP client with retry and circuit breaker.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `config: HttpConfig=None`, `base_url: str=None`, `circuit_breaker_name: str=None` | | Initialization | Initialize async client |
| | `set_auth_header` | `scheme: str`, `credentials: str` | `None` | Auth | Set authorization header |
| | `set_bearer_token` | `token: str` | `None` | Auth | Set Bearer token |
| `async` | `request` | `method: str`, `url: str`, `params: Dict=None`, `data: Any=None`, `json: Any=None`, `headers: Dict=None`, `timeout: float=None`, `raise_on_error: bool=True` | `HttpResponse` | Request | Make async HTTP request |
| `async` | `get` | `url: str`, `params: Dict=None`, `**kwargs` | `HttpResponse` | Request | GET request |
| `async` | `post` | `url: str`, `data: Any=None`, `json: Any=None`, `**kwargs` | `HttpResponse` | Request | POST request |
| `async` | `put` | `url: str`, `data: Any=None`, `json: Any=None`, `**kwargs` | `HttpResponse` | Request | PUT request |
| `async` | `patch` | `url: str`, `data: Any=None`, `json: Any=None`, `**kwargs` | `HttpResponse` | Request | PATCH request |
| `async` | `delete` | `url: str`, `**kwargs` | `HttpResponse` | Request | DELETE request |
| `async` | `head` | `url: str`, `**kwargs` | `HttpResponse` | Request | HEAD request |
| `async` | `close` | | `None` | Lifecycle | Close underlying session |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `HttpResponse`

HTTP response wrapper.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `@property` | `ok` | | `bool` | Status | Check if response is successful (2xx) |
| `@property` | `is_redirect` | | `bool` | Status | Check if response is redirect (3xx) |
| `@property` | `is_client_error` | | `bool` | Status | Check if response is client error (4xx) |
| `@property` | `is_server_error` | | `bool` | Status | Check if response is server error (5xx) |
| `@property` | `text` | | `str` | Body | Get response body as text |
| `@property` | `content_type` | | `Optional[str]` | Headers | Get Content-Type header |
| `@property` | `content_length` | | `Optional[int]` | Headers | Get Content-Length as int |
| | `json` | | `Any` | Body | Parse response as JSON |
| | `json_or_none` | | `Optional[Any]` | Body | Parse as JSON, return None on failure |
| | `header` | `name: str`, `default: str=None` | `Optional[str]` | Headers | Get header (case-insensitive) |
| | `raise_for_status` | | `None` | Errors | Raise exception if status >= 400 |
| | `to_dict` | | `Dict[str, Any]` | Serialization | Convert to dictionary |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `HttpConfig`

HTTP client configuration.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `get_read_timeout` | | `float` | Config | Get read timeout (falls back to timeout) |
| | `get_default_headers` | | `Dict[str, str]` | Config | Get default headers including User-Agent |
| `@classmethod` | `fast` | | `HttpConfig` | Factory | Config for fast internal services |
| `@classmethod` | `external_api` | | `HttpConfig` | Factory | Config for external APIs |
| `@classmethod` | `no_retry` | | `HttpConfig` | Factory | Config with no retries |

</details>

<br>

<details>
<summary><strong>Attributes</strong></summary>

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `timeout` | `float` | `30.0` | Total request timeout in seconds |
| `connect_timeout` | `float` | `10.0` | Connection timeout in seconds |
| `read_timeout` | `Optional[float]` | `None` | Read timeout (uses timeout if not set) |
| `headers` | `Dict[str, str]` | `{}` | Default headers |
| `user_agent` | `str` | `"shared-libs-http/1.0"` | User-Agent header |
| `retry` | `RetryConfig` | (default) | Retry configuration |
| `circuit_breaker` | `CircuitBreakerConfig` | (default) | Circuit breaker configuration |
| `tracing_enabled` | `bool` | `True` | Enable tracing |
| `verify_ssl` | `bool` | `True` | Verify SSL certificates |
| `follow_redirects` | `bool` | `True` | Follow redirects |
| `max_redirects` | `int` | `10` | Maximum redirects |

</details>

</div>
