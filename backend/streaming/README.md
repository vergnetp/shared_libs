# Streaming Module

Real-time event streaming infrastructure for FastAPI applications.

## Features

- **Lease-based rate limiting** - Cap concurrent streams per user
- **Redis Pub/Sub channels** - Non-blocking event delivery
- **Queue-based streaming** - Decouple work from SSE response
- **Direct streaming** - Low-latency for LLM tokens
- **OpenSearch persistence** - Optional event storage for debugging/analytics
- **Sync + Async support** - Works in both FastAPI routes and background workers

## Installation

```bash
pip install redis aioredis starlette
# Optional: pip install opensearch-py boto3 requests-aws4auth
```

## Quick Start

### Initialize (app startup)

```python
from shared_libs.backend.job_queue import QueueRedisConfig
from shared_libs.backend.streaming import init_streaming

redis_config = QueueRedisConfig(url="redis://localhost:6379/0")

init_streaming(
    redis_config,
    enable_storage=True,  # Optional: persist events to OpenSearch
)
```

### LLM Streaming (Direct, Low-Latency)

For streaming LLM tokens where latency matters:

```python
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from shared_libs.backend.streaming import stream_lease, StreamLimitExceeded

@router.post("/chat/stream")
async def chat_stream(request: ChatRequest, user: UserIdentity = Depends(get_current_user)):
    try:
        async with stream_lease(str(user.id)) as lease:
            async def generate():
                async for token in llm_client.stream(request.prompt):
                    yield f"data: {json.dumps({'token': token})}\n\n"
                    
                    # Refresh lease for long responses
                    if token_count % 100 == 0:
                        await lease.refresh_async()
                
                yield f"data: {json.dumps({'done': True})}\n\n"
            
            return StreamingResponse(generate(), media_type="text/event-stream")
    
    except StreamLimitExceeded:
        raise HTTPException(429, "Too many concurrent streams. Please wait.")
```

### Deployment Streaming (Queue-Based, Non-Blocking)

For long-running tasks where you don't want to block FastAPI workers:

```python
from shared_libs.backend.streaming import StreamContext, sse_response
from shared_libs.backend.job_queue import QueueManager

@router.post("/deploy")
async def deploy(request: DeployRequest, user: UserIdentity = Depends(get_current_user)):
    # Create context (serializable)
    ctx = StreamContext.create(
        workspace_id=str(user.id),
        project=request.project,
        env=request.environment,
        service=request.service,
        persist_events=True,  # Store in OpenSearch
    )
    
    # Enqueue background work (returns immediately)
    queue_manager.enqueue(
        entity={"stream_ctx": ctx.to_dict(), "config": request.dict()},
        processor=deploy_to_servers,
    )
    
    # Return SSE stream (subscribes to Redis Pub/Sub - non-blocking)
    return await sse_response(ctx.channel_id)


# Background worker (runs in QueueWorker)
def deploy_to_servers(entity: dict):
    ctx = StreamContext.from_dict(entity["stream_ctx"])
    config = entity["config"]
    
    ctx.log("🚀 Starting deployment...")
    ctx.progress(10, step="building")
    
    # Build image
    image = build_docker_image(config)
    ctx.progress(40, step="pushing")
    
    # Push to registry
    push_to_registry(image)
    ctx.progress(70, step="deploying")
    
    # Deploy to servers
    results = deploy_containers(image, config)
    
    # Complete stream
    ctx.complete(
        success=True,
        deployment_id="abc123",
        servers=results,
    )
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  DIRECT STREAMING (LLM)                                                 │
│                                                                         │
│  FastAPI Route ─────────────────────────────────> SSE to Client         │
│       │                                                                 │
│       └── stream_lease() limits concurrent streams                      │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│  QUEUE-BASED STREAMING (Deployments)                                    │
│                                                                         │
│  FastAPI Route                                                          │
│       │                                                                 │
│       ├── Create StreamContext                                          │
│       ├── Enqueue to job_queue (returns immediately)                    │
│       └── Return sse_response() (subscribes to Redis Pub/Sub)           │
│                      │                                                  │
│                      │ Redis Pub/Sub                                    │
│                      │                                                  │
│  Background Worker   │                                                  │
│       │              │                                                  │
│       └── ctx.emit() ────────────────────> SSE to Client                │
│                      │                                                  │
│                      └──────────────────> OpenSearch (optional)         │
└─────────────────────────────────────────────────────────────────────────┘
```

## Components

### StreamContext

Serializable context for background workers:

```python
ctx = StreamContext.create(
    workspace_id="user_123",
    project="myapp",
    env="prod",
    service="api",
    persist_events=True,
)

# Serialize for job queue
entity = {"ctx": ctx.to_dict(), ...}

# Deserialize in worker
ctx = StreamContext.from_dict(entity["ctx"])

# Emit events
ctx.log("Processing...")
ctx.progress(50, step="halfway")
ctx.error("Warning: retrying...", details={"attempt": 2})
ctx.complete(success=True, result={"id": "abc"})
```

### StreamLeaseLimiter

Rate limit concurrent streams per user:

```python
from streaming import StreamLeaseConfig, init_lease_limiter

# Configure limits
config = StreamLeaseConfig(
    limit=5,           # Max 5 concurrent streams per user
    ttl_seconds=180,   # Lease expires after 3 minutes (crash recovery)
)

init_lease_limiter(redis_config, config)
```

### Event Types

| Type | Description | Closes Stream |
|------|-------------|---------------|
| `log` | Log message for user | No |
| `progress` | Progress update (0-100) | No |
| `data` | Generic data payload | No |
| `error` | Error (recoverable) | No |
| `done` | Completion event | **Yes** |
| `ping` | Keepalive | No |

### OpenSearch Storage

Optional event persistence for debugging:

```python
from streaming import init_event_storage, get_event_storage

# Initialize
init_event_storage(
    host="localhost",
    port=9200,
    index_prefix="stream_events",
)

# Query events
storage = get_event_storage()
events = storage.query(
    channel_id="abc123",
    event_types=["log", "error"],
    limit=100,
)
```

Environment variables:
- `OPENSEARCH_HOST` - OpenSearch host
- `OPENSEARCH_PORT` - OpenSearch port  
- `OPENSEARCH_USE_SSL` - Use SSL (true/false)
- `OPENSEARCH_AUTH_TYPE` - Auth type (none/basic/aws)
- `OPENSEARCH_USERNAME` - Username for basic auth
- `OPENSEARCH_PASSWORD` - Password for basic auth

## Configuration

### Lease Limiter

```python
StreamLeaseConfig(
    limit=5,              # Max concurrent streams per user
    ttl_seconds=180,      # Lease TTL (crash recovery)
    key_namespace="stream_leases",
    key_ttl_grace=60,     # Extra key TTL after all leases expire
)
```

### Channels

```python
ChannelConfig(
    key_prefix="stream:",     # Redis channel prefix
    subscribe_timeout=1.0,    # Timeout between polls
    ping_interval=15.0,       # Keepalive ping interval
    max_idle_time=300.0,      # Close stream after 5 min idle
)
```

## Migration from Old SSEEmitter

The module includes a legacy-compatible `SSEEmitter` class:

```python
# Old code (in-memory queue, blocking)
from infra.streaming import SSEEmitter, sse_response

emitter = SSEEmitter()
emitter.log("Starting...")
return await sse_response(worker_func, emitter)

# New code (Redis Pub/Sub, non-blocking)  
from streaming import StreamContext, sse_response

ctx = StreamContext.create(...)
queue_manager.enqueue(...)
return await sse_response(ctx.channel_id)
```

For gradual migration, the `SSEEmitter` in this module publishes to Redis:

```python
from streaming import SSEEmitter, sse_response

emitter = SSEEmitter()  # Now uses Redis Pub/Sub
emitter.log("Starting...")
# ... existing code works ...
return await sse_response(emitter.channel_id)
```

---

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `StreamContext`

Serializable context for emitting events from background workers.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `@classmethod` | `create` | `workspace_id: str=None`, `project: str=None`, `env: str=None`, `service: str=None`, `persist_events: bool=False`, `**extra` | `StreamContext` | Factory | Create new context with auto-generated channel_id. |
| | `to_dict` | | `Dict[str, Any]` | Serialization | Serialize to dict for job queue. |
| `@classmethod` | `from_dict` | `data: Dict[str, Any]` | `StreamContext` | Serialization | Deserialize from dict in worker. |
| | `emit` | `event_type: str`, `**data` | `None` | Events | Emit a raw event to Redis Pub/Sub. |
| | `log` | `message: str`, `level: str="info"` | `None` | Events | Emit a log event. |
| | `progress` | `percent: int`, `step: str=None`, `message: str=None` | `None` | Events | Emit a progress event. |
| | `error` | `message: str`, `details: Dict=None` | `None` | Events | Emit an error event (doesn't close stream). |
| | `data` | `payload: Dict[str, Any]` | `None` | Events | Emit a generic data event. |
| | `complete` | `success: bool`, `error: str=None`, `**result` | `None` | Events | Emit completion and close stream. |
| | `deploy_start` | `target: str`, `server_count: int` | `None` | Deployment | Emit deployment start event. |
| | `deploy_success` | `ip: str`, `container_name: str`, `url: str=None` | `None` | Deployment | Emit successful deployment. |
| | `deploy_failure` | `ip: str`, `error: str` | `None` | Deployment | Emit failed deployment. |

</details>

<br>

<details>
<summary><strong>Properties</strong></summary>

| Property | Returns | Description |
|----------|---------|-------------|
| `channel_id` | `str` | Unique channel identifier for Pub/Sub. |
| `debug_context` | `Dict[str, Any]` | Debugging context injected into all events. |
| `namespace` | `str` | Namespace string (workspace_project_env_service). |
| `is_closed` | `bool` | Whether complete() has been called. |

</details>

<br>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `StreamLeaseLimiter`

Redis-backed concurrent stream limiter using ZSET.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `acquire_stream_lease` | `user_id: str` | `Optional[str]` | Lease | Try to acquire lease. Returns lease_id or None. |
| | `release_stream_lease` | `user_id: str`, `lease_id: str` | `None` | Lease | Release a previously acquired lease. |
| | `refresh_stream_lease` | `user_id: str`, `lease_id: str` | `bool` | Lease | Extend lease TTL. Returns False if expired. |
| | `get_active_streams` | `user_id: str` | `int` | Query | Get count of active streams for user. |
| | `get_all_leases` | `user_id: str` | `List[Tuple[str, float]]` | Query | Get all active leases (for debugging). |

</details>

<br>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `StreamLease`

Handle for an active stream lease.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `refresh` | | `bool` | Lease | Refresh lease TTL (sync). |
| `async` | `refresh_async` | | `bool` | Lease | Refresh lease TTL (async). |
| | `release` | | `None` | Lease | Explicitly release the lease (sync). |
| `async` | `release_async` | | `None` | Lease | Explicitly release the lease (async). |

</details>

<br>

<details>
<summary><strong>Properties</strong></summary>

| Property | Returns | Description |
|----------|---------|-------------|
| `lease_id` | `str` | Unique lease identifier. |
| `user_id` | `str` | User who owns the lease. |
| `is_active` | `bool` | Whether lease is still active. |

</details>

<br>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### Functions

| Function | Args | Returns | Description |
|----------|------|---------|-------------|
| `init_streaming` | `redis_config`, `lease_config=None`, `channel_config=None`, `storage_config=None`, `enable_storage=False` | `None` | Initialize all streaming components. |
| `stream_lease` | `user_id: str`, `limiter=None` | `AsyncContextManager[StreamLease]` | Async context manager for stream lifecycle. |
| `stream_lease_sync` | `user_id: str`, `limiter=None` | `ContextManager[StreamLease]` | Sync context manager for stream lifecycle. |
| `sse_response` | `channel_id: str`, `channel=None`, `headers=None` | `StreamingResponse` | Create FastAPI SSE response from Redis Pub/Sub. |
| `sse_response_with_lease` | `channel_id: str`, `user_id: str`, `channel=None`, `headers=None` | `StreamingResponse` | SSE response with automatic lease management. |
| `direct_sse_response` | `generator: AsyncIterator`, `user_id=None`, `use_lease=True`, `headers=None` | `StreamingResponse` | Create SSE response from direct async generator. |
| `get_active_streams` | `user_id: str`, `limiter=None` | `int` | Get count of active streams for user. |
| `can_start_stream` | `user_id: str`, `limiter=None` | `bool` | Check if user can start a new stream. |

</div>
