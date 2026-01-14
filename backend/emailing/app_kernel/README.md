# app_kernel

A stable, reusable application kernel for backend services.

## Overview

`app_kernel` provides runtime infrastructure that can be reused across multiple backend services (agentic or not). It handles auth, jobs, streaming safety, and observability so you don't re-implement them every time.

**Philosophy:**
- **Kernel provides:** mechanisms + invariants
- **Apps provide:** meaning + business logic
- Kernel is domain-agnostic
- All configuration is immutable after initialization

**Rule of thumb:** If it changes weekly or is product-specific, it does NOT belong in `app_kernel`.

## Installation

```python
# In your shared_libs or requirements
from app_kernel import init_app_kernel, KernelSettings
```

## 🚀 Quick Start (Easiest Way)

Create a complete service in **~30 lines** using `create_service`:

```python
from fastapi import APIRouter, Depends
from app_kernel import create_service, ServiceConfig, get_current_user

# Your business logic
router = APIRouter(prefix="/widgets", tags=["widgets"])

@router.post("")
async def create_widget(data: dict, user=Depends(get_current_user)):
    return {"id": "123", "owner": user.id, **data}

@router.get("")
async def list_widgets(user=Depends(get_current_user)):
    return []

# Create the app - that's it!
app = create_service(
    name="widget_service",
    routers=[router],
    config=ServiceConfig.from_env(),  # Uses JWT_SECRET, REDIS_URL env vars
)
```

**What you get for free:**
- ✅ JWT authentication (`/api/v1/auth/login`, `/api/v1/auth/register`)
- ✅ CORS (configured or `*`)
- ✅ Security headers
- ✅ Request ID tracking
- ✅ Structured logging
- ✅ Metrics endpoint (`/metrics`)
- ✅ Health endpoints (`/healthz`, `/readyz`)
- ✅ Rate limiting (if `REDIS_URL` set)
- ✅ Background jobs (if `REDIS_URL` set)
- ✅ Error handling

### With Background Jobs

```python
from app_kernel import create_service, ServiceConfig, get_job_client

# Task handler
async def process_order(payload, ctx):
    order_id = payload["order_id"]
    # Do work...
    return {"status": "done"}

# Route that enqueues work
router = APIRouter(prefix="/orders")

@router.post("")
async def create_order(data: dict, user=Depends(get_current_user)):
    client = get_job_client()
    result = await client.enqueue("process_order", {"order_id": "123"}, user_id=user.id)
    return {"job_id": result.job_id}

# Create app with tasks
app = create_service(
    name="order_service",
    routers=[router],
    tasks={"process_order": process_order},  # Register task handlers
    config=ServiceConfig.from_env(),
)
```

### ServiceConfig Options

```python
config = ServiceConfig(
    # Auth
    jwt_secret="your-secret",      # Required for production
    jwt_expiry_hours=24,
    auth_enabled=True,
    allow_self_signup=False,
    
    # Redis (enables jobs, rate limiting)
    redis_url="redis://localhost:6379",
    
    # CORS
    cors_origins=["http://localhost:3000"],
    
    # Rate limiting
    rate_limit_requests=100,
    rate_limit_window=60,
    
    # Debug
    debug=False,
)

# Or load from environment variables
config = ServiceConfig.from_env()  # Reads JWT_SECRET, REDIS_URL, DEBUG, etc.
```

## Advanced: Manual Initialization

For more control, use `init_app_kernel` directly:

```python
from fastapi import FastAPI
from app_kernel import init_app_kernel, KernelSettings, JobRegistry
from app_kernel.settings import AuthSettings, RedisSettings

app = FastAPI()

# 1. Create job registry and register tasks
registry = JobRegistry()

@registry.task("process_document")
async def process_document(payload, ctx):
    doc_id = payload["doc_id"]
    # Process the document...
    return {"status": "done"}

# 2. Configure settings (frozen after creation)
settings = KernelSettings(
    auth=AuthSettings(token_secret=os.environ["JWT_SECRET"]),
    redis=RedisSettings(url=os.environ["REDIS_URL"]),
)

# 3. Initialize kernel (for API process)
init_app_kernel(app, settings, registry)

# NOTE: Workers run as SEPARATE PROCESSES - see "Running Workers" section
```

## Auto-Mounted Routes

The kernel automatically mounts common routes based on `FeatureSettings`. **No manual `include_router` needed.**

### Defaults (safe for production)

| Feature | Default | Endpoint |
|---------|---------|----------|
| Health | ✅ ON | `GET /healthz`, `GET /readyz` |
| Metrics | ✅ ON (admin-protected) | `GET /metrics` |
| Auth routes | ✅ ON (if local auth) | `/auth/login`, `/auth/me`, `/auth/refresh` |
| Self-signup | ❌ OFF | `/auth/register` (disabled) |
| Audit routes | ❌ OFF | `GET /audit` |

### Configuration

```python
from app_kernel import KernelSettings, FeatureSettings

settings = KernelSettings(
    features=FeatureSettings(
        # Health endpoints (always safe, no auth)
        enable_health_routes=True,
        health_path="/healthz",
        ready_path="/readyz",
        
        # Metrics (protected by default)
        enable_metrics=True,
        metrics_path="/metrics",
        protect_metrics="admin",  # "admin", "internal", or "none"
        
        # Auth routes (for local auth mode)
        enable_auth_routes=True,
        auth_mode="local",        # "local", "apikey", or "external"
        allow_self_signup=False,  # IMPORTANT: disabled by default
        auth_prefix="/auth",
        
        # Audit (admin only, optional)
        enable_audit_routes=False,
    ),
)
```

### Environment Variable Overrides

```bash
KERNEL_ENABLE_HEALTH=true
KERNEL_ENABLE_METRICS=true
KERNEL_PROTECT_METRICS=admin
KERNEL_ENABLE_AUTH=true
KERNEL_AUTH_MODE=local
KERNEL_ALLOW_SIGNUP=false
KERNEL_ENABLE_AUDIT=false
```

### Health Checks

Configure custom health checks for `/readyz`:

```python
from functools import partial

async def check_db() -> tuple[bool, str]:
    try:
        await db.execute("SELECT 1")
        return True, "database connected"
    except Exception as e:
        return False, f"database error: {e}"

async def check_redis() -> tuple[bool, str]:
    try:
        await redis.ping()
        return True, "redis connected"
    except Exception as e:
        return False, f"redis error: {e}"

settings = KernelSettings(
    health_checks=(check_db, check_redis),
)
```

### Auth Router (User Store)

For local auth mode, provide a `UserStore` implementation:

```python
from app_kernel.auth import UserStore

class MyUserStore(UserStore):
    async def get_by_username(self, username: str) -> dict | None:
        # Return: {id, username, email, password_hash, role}
        return await db.users.find_one(username=username)
    
    async def get_by_id(self, user_id: str) -> dict | None:
        return await db.users.find_one(id=user_id)
    
    async def create(self, username: str, email: str, password_hash: str) -> dict:
        # Raise ValueError if username/email exists
        return await db.users.create(...)

init_app_kernel(
    app,
    settings,
    user_store=MyUserStore(),
    is_admin=lambda user: user.get("role") == "admin",
)
```

## Running Workers

**Workers are separate processes, not part of FastAPI startup.**

The kernel provides worker code; your deployment decides how to run workers.

```python
# worker_main.py - Run this as a separate process
import asyncio
from app_kernel.jobs import get_worker_manager

async def main():
    manager = get_worker_manager()
    await manager.start()
    
    # Block until shutdown signal
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        await manager.stop()

if __name__ == "__main__":
    # Must call init_app_kernel first to configure
    from myapp import create_app_config
    create_app_config()  # Sets up kernel
    
    asyncio.run(main())
```

Deployment options:
- Separate container/process
- Supervisor/systemd managed
- Kubernetes separate deployment
- Never inside FastAPI lifecycle

## Module Structure

```
app_kernel/
├── app.py              # init_app_kernel(...)
├── settings.py         # KernelSettings configuration
├── auth/
│   ├── deps.py         # FastAPI dependencies
│   ├── models.py       # UserIdentity, TokenPayload
│   └── utils.py        # Token/password utilities
├── access/
│   ├── workspace.py    # Workspace membership checks
│   └── scope.py        # Permission scopes
├── db/
│   └── session.py      # Database session factory
├── jobs/
│   ├── client.py       # Enqueue wrapper
│   ├── worker.py       # Worker loop
│   └── registry.py     # Task registry interface
├── streaming/
│   ├── leases.py       # StreamLeaseLimiter (Redis)
│   └── lifecycle.py    # stream_lease context manager
├── reliability/
│   ├── ratelimit.py    # Rate limiting
│   └── idempotency.py  # Request deduplication
└── observability/
    ├── logging.py      # Structured logging
    ├── metrics.py      # Metrics collection
    └── audit.py        # Audit trail
```

## Configuration

All settings are **frozen (immutable)** after creation. No per-request or runtime mutation is allowed.

### KernelSettings

```python
from app_kernel import KernelSettings
from app_kernel.settings import (
    RedisSettings,
    AuthSettings,
    JobSettings,
    StreamingSettings,
    ObservabilitySettings,
    ReliabilitySettings,
)

settings = KernelSettings(
    # Redis connection
    redis=RedisSettings(
        url="redis://localhost:6379",
        key_prefix="myapp:",
    ),
    
    # Authentication
    auth=AuthSettings(
        token_secret="your-secret-key",
        access_token_expires_minutes=15,
        refresh_token_expires_days=30,
    ),
    
    # Job queue
    jobs=JobSettings(
        worker_count=4,
        thread_pool_size=8,
        max_attempts=3,  # Advisory default, not enforced by kernel
    ),
    
    # Streaming limits
    streaming=StreamingSettings(
        max_concurrent_per_user=5,
        lease_ttl_seconds=180,
    ),
    
    # Logging/metrics
    observability=ObservabilitySettings(
        service_name="my-service",
        log_level="INFO",
    ),
    
    # Rate limiting
    reliability=ReliabilitySettings(
        rate_limit_requests=100,
        rate_limit_window_seconds=60,
    ),
)
```

## Authentication

### FastAPI Dependencies

```python
from fastapi import Depends
from app_kernel.auth import get_current_user, require_admin, UserIdentity

@app.get("/profile")
async def get_profile(user: UserIdentity = Depends(get_current_user)):
    return {"id": user.id, "email": user.email}

@app.post("/admin/action")
async def admin_action(user: UserIdentity = Depends(require_admin)):
    return {"admin_id": user.id}
```

### Token Utilities

```python
from app_kernel.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

# Hash password
hashed = hash_password("user-password")

# Verify password
is_valid = verify_password("user-password", hashed)

# Create tokens
access = create_access_token(user, secret, expires_delta=timedelta(minutes=15))
refresh = create_refresh_token(user, secret, expires_delta=timedelta(days=30))
```

## Jobs

### Registering Tasks

Registry metadata (`timeout`, `max_attempts`) is **advisory only**. The kernel does not act as a scheduler - it dispatches work to registered processors and fails fast on unknown task names.

```python
from app_kernel.jobs import JobRegistry, JobContext

registry = JobRegistry()

@registry.task("send_email", timeout=30.0, max_attempts=3)
async def send_email(payload: dict, ctx: JobContext) -> dict:
    email = payload["email"]
    subject = payload["subject"]
    
    # ctx contains job metadata
    print(f"Job {ctx.job_id}, attempt {ctx.attempt}/{ctx.max_attempts}")
    
    # Send email...
    return {"sent": True}

# Or register manually
registry.register("process_file", process_file_handler)
```

### Enqueueing Jobs

```python
from app_kernel.jobs import get_job_client

client = get_job_client()

# Simple enqueue
result = await client.enqueue(
    "send_email",
    {"email": "user@example.com", "subject": "Hello"},
)

# With options
result = await client.enqueue(
    "process_document",
    {"doc_id": "123"},
    priority="high",
    user_id=current_user.id,
    timeout=60.0,
    max_attempts=5,
)

# Batch enqueue
results = await client.enqueue_many(
    "send_notification",
    [{"user_id": "1"}, {"user_id": "2"}, {"user_id": "3"}],
    priority="low",
)
```

## Streaming

### Safe Streaming with Leases

```python
from app_kernel.streaming import stream_lease, StreamLimitExceeded
from fastapi import HTTPException

@app.post("/chat/stream")
async def stream_chat(user: UserIdentity = Depends(get_current_user)):
    try:
        async with stream_lease(user.id) as lease:
            async for chunk in generate_response():
                yield chunk
                
                # Optional: refresh for long streams
                if should_refresh:
                    lease.refresh()
                    
    except StreamLimitExceeded:
        raise HTTPException(429, "Too many concurrent streams")
```

### Check Stream Availability

```python
from app_kernel.streaming import can_start_stream, get_active_streams

# Check before starting
if await can_start_stream(user.id):
    # Start stream...
    pass

# Get count
active = await get_active_streams(user.id)
```

## Rate Limiting

```python
from app_kernel.reliability import rate_limit
from fastapi import Depends

@app.post("/api/action")
async def action(_: None = Depends(rate_limit(requests=10, window=60))):
    # Max 10 requests per 60 seconds
    return {"status": "ok"}

# Custom key function
def by_api_key(request, user):
    return f"api_key:{request.headers.get('x-api-key')}"

@app.post("/api/external")
async def external(_: None = Depends(rate_limit(100, 60, key_func=by_api_key))):
    return {"status": "ok"}
```

## Observability

### Logging with Context

```python
from app_kernel.observability import get_logger, log_context

logger = get_logger()

# Log with explicit fields
logger.info("Processing request", user_id="123", action="create")

# Log with context manager
with log_context(request_id="abc", user_id="123"):
    logger.info("Step 1")  # Includes request_id and user_id
    logger.info("Step 2")  # Includes request_id and user_id
```

### Metrics

```python
from app_kernel.observability import get_metrics

metrics = get_metrics()

# Counter
metrics.increment("api.requests", tags={"endpoint": "/users"})

# Gauge
metrics.gauge("connections.active", 42)

# Timer
with metrics.timer("api.response_time"):
    response = await process_request()
```

### Audit Logging

```python
from app_kernel.observability import get_audit

audit = get_audit()

await audit.log(
    action="user.login",
    actor_id=user.id,
    resource_type="session",
    resource_id=session.id,
    metadata={"ip": request.client.host},
)
```

### Request Metrics (Performance Monitoring)

Automatically capture rich metadata for every HTTP request. Stored asynchronously via job queue (non-blocking).

**Enable in manifest.yaml:**

```yaml
observability:
  request_metrics:
    enabled: true  # Requires Redis for async storage
    exclude_paths:
      - /health
      - /healthz
      - /readyz
      - /metrics
      - /favicon.ico
```

**What's captured:**
- Request: method, path, query_params, request_id
- Response: status_code, error details
- Timing: server_latency_ms
- Client: real IP (behind CF/nginx), user_agent, referer
- Auth: user_id, workspace_id (if authenticated)
- Geo: country (from Cloudflare CF-IPCountry header)
- Partitioning: timestamp, year, month, day, hour

**Query metrics:**

```python
from app_kernel.observability import RequestMetricsStore

store = RequestMetricsStore()

# Recent requests
metrics = await store.get_recent(limit=100, path_prefix="/api/v1/infra")

# Aggregated stats (last 24h)
stats = await store.get_stats(hours=24)
# Returns: total_requests, avg_latency_ms, slow_endpoints, error_endpoints

# Find slow requests (>1000ms)
slow = await store.get_recent(min_latency_ms=1000)
```

**API Endpoints (auto-mounted when enabled):**
- `GET /metrics/requests` - List recent requests
- `GET /metrics/requests/stats` - Aggregated statistics
- `GET /metrics/requests/slow` - Slow requests (>1s)
- `GET /metrics/requests/errors` - Error requests (4xx/5xx)

## Access Control

The kernel provides **mechanisms** for access control. It does **not** define what a "workspace" or "scope" means - that is app domain logic.

- **Kernel provides:** protocol interfaces, dependency wrappers, enforcement of app decisions
- **Apps provide:** semantic meaning, database queries, business rules

### Workspace Membership

The kernel does not define workspace semantics. Apps implement the protocol; kernel enforces app-provided decisions.

```python
from app_kernel.access import require_workspace_member, workspace_access

# Implement your checker (this is YOUR domain logic)
class MyWorkspaceChecker:
    async def is_member(self, user_id: str, workspace_id: str) -> bool:
        # YOUR definition of what "member" means
        return await db.check_membership(user_id, workspace_id)
    
    async def is_owner(self, user_id: str, workspace_id: str) -> bool:
        return await db.check_owner(user_id, workspace_id)
    
    async def get_role(self, user_id: str, workspace_id: str) -> str:
        return await db.get_role(user_id, workspace_id)

# Register with kernel (kernel will call YOUR checker)
workspace_access.set_checker(MyWorkspaceChecker())

# Use in routes (kernel enforces YOUR decision)
@app.get("/workspace/{workspace_id}/data")
async def get_data(
    workspace_id: str,
    user: UserIdentity = Depends(require_workspace_member),
):
    return {"data": "..."}
```

### Scope-Based Permissions

```python
from app_kernel.access import require_scope, check_scope

@app.delete("/documents/{doc_id}")
async def delete_doc(
    doc_id: str,
    user: UserIdentity = Depends(require_scope("delete", "document", "doc_id")),
):
    return {"deleted": True}

# Programmatic check
if await check_scope(user.id, "write", "project", project_id):
    # User has write access
    pass
```

## Strict Rules

### Separation of Concerns

| Kernel provides | Apps provide |
|-----------------|--------------|
| Mechanisms | Meaning |
| Invariants | Business logic |
| Protocol interfaces | Implementations |
| Enforcement | Decisions |

### Invariants

1. **app_kernel never imports app code** - Apps may import app_kernel, not vice versa
2. **Task processors live ONLY in apps** - Kernel dispatches, apps implement
3. **Kernel code must be safe to reuse unchanged across apps**
4. **All configuration is immutable after initialization** - No per-request mutation
5. **Streaming safety works across multiple servers** - Redis-backed, not process-local
6. **Workers are separate processes** - Never inside FastAPI lifecycle
7. **Registry metadata is advisory** - Kernel is not a scheduler
8. **Kernel does not define domain semantics** - "Workspace", "scope", etc. are app concepts

## Success Criteria

After using this kernel, a new backend service can be created by:
1. Importing app_kernel
2. Defining tasks (app provides meaning)
3. Implementing access checkers (app provides decisions)
4. Registering them with kernel (kernel enforces)
5. Deploying workers separately (deployment decides how)

No infrastructure re-implementation is needed. Streaming, jobs, auth, and safety work out of the box.

---

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `KernelSettings`

Complete configuration for app_kernel initialization.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `redis` | | `RedisSettings` | Configuration | Redis connection settings for jobs and streaming. |
| | `streaming` | | `StreamingSettings` | Configuration | Streaming lifecycle settings. |
| | `jobs` | | `JobSettings` | Configuration | Job queue settings. |
| | `auth` | | `AuthSettings` | Configuration | Auth configuration. |
| | `observability` | | `ObservabilitySettings` | Configuration | Logging and metrics settings. |
| | `reliability` | | `ReliabilitySettings` | Configuration | Rate limiting and idempotency settings. |
| | `database_url` | | `Optional[str]` | Configuration | Database URL for auth stores. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `redis: RedisSettings=None`, `streaming: StreamingSettings=None`, `jobs: JobSettings=None`, `auth: AuthSettings=None`, `observability: ObservabilitySettings=None`, `reliability: ReliabilitySettings=None`, `database_url: str=None` | | Initialization | Initializes kernel settings with all configuration components. |
| | `__post_init__` | | | Validation | Validates settings after initialization. |

</details>

<br>


</div>



<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `FeatureSettings`

Feature flags for auto-mounted kernel routers.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `enable_health_routes` | | `bool` | Feature | Enable health endpoints (/healthz, /readyz). Default: True |
| | `health_path` | | `str` | Feature | Path for liveness probe. Default: "/healthz" |
| | `ready_path` | | `str` | Feature | Path for readiness probe. Default: "/readyz" |
| | `enable_metrics` | | `bool` | Feature | Enable metrics endpoint. Default: True |
| | `metrics_path` | | `str` | Feature | Path for Prometheus metrics. Default: "/metrics" |
| | `protect_metrics` | | `Literal["admin", "internal", "none"]` | Feature | Metrics protection level. Default: "admin" |
| | `enable_auth_routes` | | `bool` | Feature | Enable auth endpoints (login, me, register). Default: True |
| | `auth_mode` | | `Literal["local", "apikey", "external"]` | Feature | Authentication mode. Default: "local" |
| | `allow_self_signup` | | `bool` | Feature | Enable /auth/register. Default: False (important!) |
| | `auth_prefix` | | `str` | Feature | URL prefix for auth routes. Default: "/auth" |
| | `enable_audit_routes` | | `bool` | Feature | Enable audit query endpoint. Default: False |
| | `audit_path` | | `str` | Feature | Path for audit endpoint. Default: "/audit" |
| `@classmethod` | `from_env` | | `FeatureSettings` | Factory | Create settings from environment variables. |

</details>

<br>

</div>



<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `JobRegistry`

Registry mapping task names to processor functions.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `register` | `name: str`, `processor: ProcessorFunc`, `timeout: float=None`, `max_attempts: int=None`, `description: str=None` | `None` | Registration | Register a task processor with optional configuration. |
| | `task` | `name: str`, `timeout: float=None`, `max_attempts: int=None`, `description: str=None` | `Callable` | Registration | Decorator for registering a task processor. |
| | `get` | `name: str` | `Optional[ProcessorFunc]` | Query | Get a processor by name. |
| | `get_metadata` | `name: str` | `Optional[Dict]` | Query | Get metadata for a task. |
| | `has` | `name: str` | `bool` | Query | Check if a task is registered. |
| | `tasks` | | `Dict[str, ProcessorFunc]` | Query | Get all registered tasks (read-only view). |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | | | Initialization | Initializes empty registry. |
| | `__contains__` | `name: str` | `bool` | Query | Check if task name in registry. |
| | `__len__` | | `int` | Query | Get number of registered tasks. |
| | `__iter__` | | `Iterator` | Query | Iterate over task names. |

</details>

<br>


</div>



<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `JobClient`

Client for enqueueing jobs.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `async` | `enqueue` | `task_name: str`, `payload: Dict`, `job_id: str=None`, `priority: str="normal"`, `user_id: str=None`, `timeout: float=None`, `max_attempts: int=None`, `delay_seconds: float=None`, `metadata: Dict=None`, `on_success: str=None`, `on_failure: str=None` | `EnqueueResult` | Enqueue | Enqueue a job for processing. |
| `async` | `enqueue_many` | `task_name: str`, `payloads: list[Dict]`, `priority: str="normal"`, `user_id: str=None` | `list[EnqueueResult]` | Enqueue | Enqueue multiple jobs efficiently. |
| `async` | `get_queue_status` | | `Dict[str, Any]` | Status | Get status of all queues. |

</details>

<br>


</div>



<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `StreamLeaseLimiter`

Lease-based concurrent stream limiter using Redis.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `acquire_stream_lease` | `user_id: str` | `Optional[str]` | Lease | Try to acquire a lease for user. Returns lease_id if allowed, else None. |
| | `release_stream_lease` | `user_id: str`, `lease_id: str` | `None` | Lease | Release a previously acquired lease. |
| | `refresh_stream_lease` | `user_id: str`, `lease_id: str` | `bool` | Lease | Extend lease while streaming continues. Returns False if expired. |
| | `get_active_streams` | `user_id: str` | `int` | Query | Returns number of active (non-expired) leases. |

</details>

<br>


</div>



<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `UserIdentity`

Core user identity for auth primitives.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `@property` | `is_admin` | | `bool` | Query | Check if user has admin role. |
| | `to_dict` | | `dict` | Serialization | Convert to dictionary. |
| `@classmethod` | `from_dict` | `data: dict` | `UserIdentity` | Serialization | Create from dictionary. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `id: str=auto`, `email: str=""`, `role: str="user"`, `is_active: bool=True`, `created_at: datetime=auto` | | Initialization | Initializes user identity. |

</details>

<br>


</div>



<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `RateLimiter`

Redis-backed sliding window rate limiter.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `check` | `identifier: str`, `limit: int=None`, `window: int=None` | `bool` | Limit | Check if request is allowed and record it. Returns True if allowed. |
| | `get_remaining` | `identifier: str`, `limit: int=None`, `window: int=None` | `int` | Query | Get remaining requests in window. |
| | `reset` | `identifier: str` | `None` | Admin | Reset rate limit for identifier. |

</details>

<br>


</div>



<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `AuditLogger`

Audit logger for recording security events.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `set_store` | `store: AuditStore` | `None` | Configuration | Set the audit store implementation. |
| `async` | `log` | `action: str`, `status: str="success"`, `actor_id: str=None`, `actor_type: str="user"`, `resource_type: str=None`, `resource_id: str=None`, `request_id: str=None`, `ip_address: str=None`, `user_agent: str=None`, `metadata: Dict=None` | `AuditEvent` | Logging | Log an audit event. |
| `async` | `query` | `actor_id: str=None`, `action: str=None`, `resource_type: str=None`, `resource_id: str=None`, `after: datetime=None`, `before: datetime=None`, `limit: int=100` | `List[AuditEvent]` | Query | Query audit events. |

</details>

<br>


</div>
