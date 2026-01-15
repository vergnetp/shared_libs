# Tracing Module

Request-scoped distributed tracing for correlating operations within a request.

## Overview

The tracing module provides lightweight, request-scoped tracing that allows you to:
- Correlate all operations within a single request
- Track timing of HTTP calls, database queries, and internal operations
- Identify slow and failed operations
- Debug issues in production

## Quick Start

### In Middleware (Automatic with app_kernel)

```python
from tracing import RequestContext, set_context, clear_context

@app.middleware("http")
async def tracing_middleware(request, call_next):
    ctx = RequestContext.create(
        request_id=request.state.request_id,
        method=request.method,
        path=request.url.path,
    )
    set_context(ctx)
    
    try:
        response = await call_next(request)
        return response
    finally:
        ctx.end()
        
        # Save traces for slow/error requests
        if ctx.has_errors or ctx.duration_ms > 1000:
            save_traces(ctx)
        
        clear_context()
```

### In Your Code - Context Manager

```python
from tracing import get_context, SpanKind

async def get_user(user_id: str):
    ctx = get_context()
    
    with ctx.span("fetch_user", SpanKind.DATABASE) as span:
        span.set_attribute("user_id", user_id)
        user = await db.get_user(user_id)
        span.set_attribute("found", user is not None)
        return user
```

### Using the Decorator

```python
from tracing import traced, SpanKind

@traced("external_api", SpanKind.HTTP_CLIENT)
async def call_stripe(customer_id: str):
    # Span automatically created and ended
    return await stripe_client.get_customer(customer_id)

@traced()  # Uses function name automatically
def calculate_total(items):
    return sum(item.price for item in items)
```

## Key Concepts

### RequestContext

Created per request, holds all spans for that request lifecycle.

```python
ctx = RequestContext.create(
    request_id="abc123",
    method="GET",
    path="/api/users",
    user_id="user_456",
)

# Access collected data
spans = ctx.get_spans()
slow_spans = ctx.get_slow_spans(threshold_ms=100)
error_spans = ctx.get_error_spans()

# Check status
if ctx.has_errors:
    print(f"Request failed after {ctx.duration_ms}ms")
```

### Span

A single traced operation with timing, status, and attributes.

```python
span = ctx.start_span("operation_name", SpanKind.HTTP_CLIENT)

try:
    result = do_work()
    span.set_attribute("result_size", len(result))
    span.set_status(SpanStatus.OK)
except Exception as e:
    span.record_error(e)
    raise
finally:
    span.end()
```

### SpanKind

Type of operation being traced:

| Kind | Usage |
|------|-------|
| `INTERNAL` | Internal function calls |
| `HTTP_CLIENT` | Outbound HTTP requests |
| `HTTP_SERVER` | Inbound HTTP requests |
| `DATABASE` | Database queries |
| `CACHE` | Cache operations |
| `QUEUE` | Message queue operations |

### SpanStatus

Outcome of a traced operation:

| Status | Meaning |
|--------|---------|
| `OK` | Operation completed successfully |
| `ERROR` | Operation failed |
| `TIMEOUT` | Operation timed out |
| `CANCELLED` | Operation was cancelled |

## Integration with HTTP Module

The `http/` module automatically creates spans for each request:

```python
from http import AsyncHttpClient

async with AsyncHttpClient() as client:
    # Span automatically created: "HTTP GET"
    response = await client.get("https://api.example.com/users")
```

## Thread/Async Safety

The module uses context variables for async-safe storage:
- Works correctly in async code with `asyncio`
- Falls back to thread-local storage for sync code
- Spans are automatically nested based on call stack

## Best Practices

1. **Create spans for external calls**: HTTP, database, cache, message queues
2. **Add meaningful attributes**: IDs, counts, flags that help debugging
3. **Use appropriate SpanKind**: Helps with filtering and visualization
4. **Don't over-trace**: Internal loops don't need spans
5. **Keep attribute values small**: Truncate large strings

---

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `Span`

A single traced operation with timing and attributes.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `@property` | `duration_ms` | | `Optional[float]` | Timing | Duration in milliseconds (None if not ended) |
| `@property` | `is_error` | | `bool` | Status | Check if span ended in error |
| | `end` | | `None` | Lifecycle | Mark span as complete |
| | `set_status` | `status: SpanStatus` | `None` | Status | Set span status |
| | `set_attribute` | `key: str`, `value: Any` | `None` | Attributes | Set a custom attribute |
| | `set_attributes` | `attrs: Dict[str, Any]` | `None` | Attributes | Set multiple custom attributes |
| | `record_error` | `error: Exception` | `None` | Errors | Record an error on this span |
| | `add_event` | `name: str`, `attributes: Dict[str, Any]=None` | `None` | Events | Add a timestamped event |
| | `to_dict` | | `Dict[str, Any]` | Serialization | Convert span to dictionary |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `RequestContext`

Context for a single request lifecycle.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `@classmethod` | `create` | `request_id: str=None`, `method: str=None`, `path: str=None`, `user_id: str=None`, `workspace_id: str=None` | `RequestContext` | Factory | Create a new request context |
| `@property` | `duration_ms` | | `Optional[float]` | Timing | Total request duration in milliseconds |
| `@property` | `has_errors` | | `bool` | Status | Check if any span has errors |
| | `start_span` | `name: str`, `kind: SpanKind=INTERNAL`, `attributes: Dict[str, Any]=None` | `Span` | Spans | Start a new span (must call .end()) |
| | `span` | `name: str`, `kind: SpanKind=INTERNAL`, `attributes: Dict[str, Any]=None` | `Generator[Span]` | Spans | Context manager for creating spans |
| | `end` | | `None` | Lifecycle | Mark request context as complete |
| | `get_spans` | | `List[Span]` | Query | Get all collected spans |
| | `get_slow_spans` | `threshold_ms: float=100` | `List[Span]` | Query | Get spans slower than threshold |
| | `get_error_spans` | | `List[Span]` | Query | Get all spans with errors |
| | `to_dict` | | `Dict[str, Any]` | Serialization | Convert context to dictionary |
| | `summary` | | `Dict[str, Any]` | Serialization | Get summary without full span details |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### Functions

| Function | Args | Returns | Description |
|----------|------|---------|-------------|
| `get_context` | | `Optional[RequestContext]` | Get current request context |
| `set_context` | `ctx: RequestContext` | `None` | Set current request context |
| `clear_context` | | `None` | Clear current request context |
| `traced` | `name: str=None`, `kind: SpanKind=INTERNAL`, `record_args: bool=False`, `record_result: bool=False` | `Decorator` | Decorator to trace function execution |

</details>

</div>
