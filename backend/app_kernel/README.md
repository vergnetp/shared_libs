# app_kernel

Production-ready FastAPI services in one function call. Everything explicit, no hidden config.

## Quick Start

```python
from app_kernel import create_service

app = create_service(
    name="my-api",
    database_url="postgresql://user:pass@localhost:5432/mydb",
    redis_url="redis://localhost:6379",
    jwt_secret="your-32-character-secret-key-here",
    cors_origins=["https://myapp.com"],
    routers=[my_router],
)
```

You get: Auth, SaaS (workspaces/teams), background jobs, rate limiting, caching, audit logging, action replay (frontend error diagnosis), health checks, metrics.

## Quick Start (Alternative APIs)

### `quick_service()` — Zero Config

For prototyping or internal tools where you don't need any configuration:

```python
from app_kernel import quick_service

app = quick_service("my-api", routers=[my_router])
```

Uses SQLite, fakeredis, and dev defaults. Equivalent to `create_service(name="my-api", config=ServiceConfig(), routers=[...])`.

### `ServiceConfig.from_env()` — Environment Variables

Load all configuration from environment variables:

```python
from app_kernel import create_service, ServiceConfig

app = create_service(
    name="my-api",
    config=ServiceConfig.from_env(),
    routers=[my_router],
)
```

Reads: `JWT_SECRET`, `DATABASE_URL`, `REDIS_URL`, `CORS_ORIGINS` (comma-separated), `DEBUG`, `LOG_LEVEL`, `RATE_LIMIT_RPM`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`.

### `init_app_kernel()` — Low-Level API

For full control, use `KernelSettings` + `init_app_kernel()` directly:

```python
from fastapi import FastAPI
from app_kernel import init_app_kernel, KernelSettings, AuthSettings, RedisSettings, FeatureSettings, JobRegistry

app = FastAPI()
registry = JobRegistry()

@registry.task("process_document")
async def process_document(payload, ctx):
    ...

settings = KernelSettings(
    auth=AuthSettings(token_secret="your-secret-key-32-chars-minimum"),
    redis=RedisSettings(url="redis://localhost:6379"),
    features=FeatureSettings(
        allow_self_signup=False,
        protect_metrics="admin",
    ),
)

init_app_kernel(app, settings, job_registry=registry, user_store=my_user_store)
```

Access the kernel runtime later via `get_kernel(app)`:

```python
from app_kernel import get_kernel

kernel = get_kernel(app)
logger = kernel.logger
metrics = kernel.metrics
```

## Typical Route Pattern

```python
from fastapi import APIRouter, Depends
from app_kernel import (
    get_current_user,
    get_current_user_optional,
    require_admin,
    cached,
    rate_limit,
    UserIdentity,
)
from app_kernel.db import db_context

router = APIRouter()

# Public endpoint - no auth required
@router.get("/products")
async def list_products():
    return await Product.find()

# Authenticated endpoint - user required
@router.get("/me")
async def get_profile(
    user: UserIdentity = Depends(get_current_user),
):
    return await User.get(id=user.id)

# Optional auth - user may or may not be logged in
@router.get("/feed")
async def get_feed(
    user: UserIdentity = Depends(get_current_user_optional),
):
    if user:
        return await get_personalized_feed(user.id)
    return await get_public_feed()

# Admin only endpoint
@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    admin: UserIdentity = Depends(require_admin),
):
    await User.soft_delete(id=user_id)
    return {"deleted": True}

# With caching
@router.get("/stats")
@cached(ttl=60, key="stats:global")
async def get_stats():
    return await compute_expensive_stats()

# With custom rate limit + batching (explicit db_context)
@router.post("/export")
@rate_limit(5)  # Only 5 requests/minute
async def export_data(
    user: UserIdentity = Depends(get_current_user),
):
    async with db_context() as db:
        return await generate_export(db, user.id)
```

### Key Dependencies

| Dependency | Description |
|------------|-------------|
| `get_current_user` | Requires auth, returns `UserIdentity`, raises 401 if not logged in |
| `get_current_user_optional` | Returns `UserIdentity` or `None` |
| `require_admin` | Requires admin user, raises 401/403 if not authorized |

## Database & Entities

The approach is code-first: define your data models as dataclasses with the `@entity` decorator, and the database schema is created and maintained automatically. No SQL migrations to write, no store layer, no repository pattern.

On startup, the kernel compares your `@entity` classes against the actual database schema. New tables and columns are created automatically. Renames and deletions are flagged with warnings — the migrator won't drop data without explicit opt-in. Migration scripts are persisted in `.data/migrations_audit/` for traceability. A full backup (native + CSV) is taken before any migration runs, stored in `.data/backups/`.

Migrations are **forward-only**. There is no `migrate down` command. To rollback: redeploy the previous version of your code and restore from backup (see [Backup & Restore](#backup--restore)). The pre-migration backup exists precisely for this. Use `POST /admin/db/restore/full` for full rollback, or `POST /admin/db/restore/revert` for quick single-table undo via history tables.


Connections are handled automatically: each entity call acquires a short-lived connection from the pool and releases it immediately. The pool is backed by the database you pass to `create_service` via `database_url` (or SQLite in `./data/{app_name}.db` if none specified). Every entity also gets free history tracking — every change is versioned automatically.

```python
from dataclasses import dataclass
from typing import Optional, List
from databases import entity, entity_field

@entity(table="products")
@dataclass
class Product:
    name: str = entity_field(nullable=False)
    price: int = entity_field(default=0)
    tags: List[str] = entity_field(default=None)  # JSON auto-serialized
    workspace_id: str = entity_field(index=True)
    id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    deleted_at: Optional[str] = None

# Just use it — no db parameter needed:
product = await Product.save(data={"name": "Widget", "price": 999, "workspace_id": "ws1"})
product = await Product.get(id="abc-123")
products = await Product.find(where="workspace_id = ?", params=("ws1",))
await Product.update(id="abc-123", data={"price": 1299})
await Product.soft_delete(id="abc-123")
count = await Product.count(where="price > ?", params=(500,))

# Upsert by matching on fields instead of id:
await Product.save(
    data={"name": "Widget", "workspace_id": "ws1", "price": 1499},
    match_by=["workspace_id", "name"],  # updates if exists, creates if not
)

# History — every change is versioned automatically:
versions = await Product.history(id="abc-123")       # all versions, newest first
original = await Product.get_version(id="abc-123", version=1)  # first version
```

If you want to batch multiple operations on one connection (for performance or atomicity), group them in a `db_context` block:

```python
from app_kernel.db import db_context

async with db_context() as db:
    product = await Product.get(db, id="abc-123")
    await Order.save(db, data={"product_id": product.id, "qty": 2})
    await Product.update(db, id=product.id, data={"stock": product.stock - 2})
```

Operations are retried 3 times with exponential backoff, with circuit breaker protection against cascading failures.

### entity_field Options

All options are enforced at the database level (as column constraints in the DDL), not just in application code.

```python
name: str = entity_field(
    default="untitled",                             # DEFAULT clause
    nullable=False,                                 # NOT NULL
    unique=True,                                    # UNIQUE constraint
    index=True,                                     # CREATE INDEX
    check="[status] IN ('draft', 'published')",     # CHECK constraint (use [col] for column refs)
    renamed_from="old_name",                        # Safe column rename (copies data, keeps old column)
)
```

| Option | Type | Default | Effect |
|--------|------|---------|--------|
| `default` | any | `None` | `DEFAULT` clause on the column |
| `nullable` | `bool` | `True` | `NOT NULL` when `False` |
| `unique` | `bool` | `False` | `UNIQUE` constraint |
| `index` | `bool` | `False` | Creates a standalone index |
| `check` | `str` | `None` | `CHECK (...)` constraint. Reference columns with bracket syntax `[col_name]` — brackets are converted to the correct quoting for each backend. |
| `renamed_from` | `str` | `None` | Previous column name. Migrator adds the new column and copies data from the old one. Old column is kept for safe rollback. Remove this parameter once the rename is fully deployed. |

### Seeding

Use `db_seed` to populate initial data. `match_by` makes seeding idempotent — safe to run on every restart:

```python
async def seed_data(db):
    await Product.save(db, data={
        "slug": "pro",
        "name": "Pro Plan",
        "price": 1999,
    }, match_by="slug")  # won't duplicate on restart

app = create_service(
    ...
    db_seed=seed_data,
)
```

## Configuration

All configuration is explicit - pass it to `create_service()`. Only `APP_ENV` (fallback: `ENV`) is read from the environment (defaults to `prod`).

### .env loading (optional)

If `python-dotenv` is installed, the kernel auto-loads `.env` files **and** `.env.{ENV}` files (when present), with inheritance.

Search locations (defaults → overrides):
1. **Grandparent** directory: `.env`, then `.env.{ENV}`
2. **Parent** directory: `.env`, then `.env.{ENV}`
3. **Sibling** (next to the file that called `create_service`, usually your `main.py`): `.env`, then `.env.{ENV}`

**Priority:** `os.environ` always wins, then sibling, then parent, then grandparent.  
Within each directory, `.env.{ENV}` overrides `.env`.

So you can put shared defaults higher up, and override per-app/service closer to your `main.py` file (or via real environment variables in prod).


### Minimal (Development)

```python
app = create_service(
    name="my-api",
    routers=[my_router],
)
```

Uses SQLite and fakeredis automatically in dev.

### Production

```python
app = create_service(
    name="my-api",
    version="2.1.0",
    
    # Infrastructure (required in prod)
    database_url="postgresql://user:pass@db:5432/myapp",
    redis_url="redis://redis:6379",
    
    # Auth (required in prod)
    jwt_secret="your-very-long-secret-key-at-least-32-chars",
    cors_origins=["https://myapp.com"],
    
    # Your app
    routers=[users_router, orders_router],
    tasks={
        "send_email": send_email_handler,
        "process_order": process_order_handler,
    },
    
    # Schema & Seed
    schema_init=init_app_tables,
    db_seed=seed_admin_user,
    
    # Optional integrations
    oauth_google=("client_id", "client_secret"),
    oauth_github=("client_id", "client_secret"),
    stripe_secret_key="sk_live_...",
    smtp_url="smtp://user:pass@smtp.example.com:587",
    email_from="My App <noreply@myapp.com>",
    
    # Health checks
    health_checks=[
        ("postgres", check_db),
        ("redis", check_redis),
    ],
)
```

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | required | Service name (logs, metrics) |
| `version` | `str` | `"1.0.0"` | Service version |
| `description` | `str` | `""` | API description for docs |
| `api_prefix` | `str` | `"/api/v1"` | Prefix for app routers |
| `routers` | `list` | `[]` | FastAPI routers |
| `tasks` | `dict` | `None` | Background job handlers |
| `database_url` | `str` | `None` | Database connection string |
| `redis_url` | `str` | `None` | Redis connection string |
| `schema_init` | `callable` | `None` | Async function to init tables |
| `db_seed` | `callable` | `None` | Async function to seed data |
| `jwt_secret` | `str` | `None` | JWT signing secret (32+ chars) |
| `allow_self_signup` | `bool` | `True` | Allow open registration |
| `rate_limit_anonymous_rpm` | `int` | `30` | Requests/min for anonymous |
| `rate_limit_authenticated_rpm` | `int` | `120` | Requests/min for authenticated |
| `rate_limit_admin_rpm` | `int` | `600` | Requests/min for admin |
| `cors_origins` | `list` | `None` | Allowed CORS origins |
| `oauth_google` | `tuple` | `None` | `(client_id, client_secret)` |
| `oauth_github` | `tuple` | `None` | `(client_id, client_secret)` |
| `stripe_secret_key` | `str` | `None` | Stripe API key |
| `smtp_url` | `str` | `None` | SMTP server URL |
| `email_from` | `str` | `None` | Sender address |
| `env_checks` | `list` | `None` | Custom startup validation |
| `health_checks` | `list` | `[]` | Custom health checks |
| `on_startup` | `callable` | `None` | Startup hook |
| `on_shutdown` | `callable` | `None` | Shutdown hook |
| `backup_schedule` | `str` | `"0 15 * * *"` | Cron schedule for periodic backups (5-field cron, UTC). `None` to disable. |
| `test_runners` | `list` | `None` | Self-test runner functions |
| `debug` | `bool` | `False` | Debug mode (forced False in prod) |

## Environment Detection

The only environment variable read is `ENV`:

| ENV Value | Behavior |
|-----------|----------|
| `prod` (or not set) | Production checks enforced |
| `dev`, `development`, `local` | Relaxed validation |
| `uat`, `staging`, `test` | Relaxed validation |

## Production Checks

When `ENV=prod` (or not set), enforced at startup:

1. `database_url` set and not SQLite
2. `redis_url` set
3. `jwt_secret` 32+ characters
4. `cors_origins` explicit (not wildcard)
5. If `smtp_url` set, `email_from` required

## Redis Architecture

The kernel uses Redis for two distinct purposes with two separate client types:

### Kernel Internals (async Redis)

All kernel observability flows through one unified pattern: **push to Redis list → admin worker drains in batches → save to DB**. A single shared async Redis client is created at startup and injected into all publishers.

| Publisher | Redis Key | DB Table |
|-----------|-----------|----------|
| Audit (`AuditWrappedConnection`) | `admin:audit_events` | `kernel_audit_logs` |
| Usage Metering (`UsageMeteringMiddleware`) | `admin:metering_events` | `kernel_usage_events` |
| Request Metrics (`RequestMetricsMiddleware`) | `admin:request_metrics` | `kernel_request_metrics` |

Other kernel consumers of the shared async client:
- **Rate limiter** — sliding window counters
- **Idempotency** — request deduplication keys with TTL
- **Cache** — `RedisCache` backend for `@cached` decorator

### App-Level Jobs (sync Redis)

The `job_queue` library uses sync Redis for background task processing (deploy services, send emails, process documents). The kernel creates a sync Redis client and injects it into `QueueRedisConfig(client=...)` — the job_queue never creates its own connection.

### Fakeredis Fallback

Both client types use singleton fakeredis instances when real Redis is unavailable. All Redis clients are created through `dev_deps.py`:

| Function | Type | Singleton |
|----------|------|-----------|
| `get_async_redis_client(url)` | `redis.asyncio` / `fakeredis.aioredis` | `_fakeredis_async_instance` |
| `get_sync_redis_client(url)` | `redis.Redis` / `fakeredis.FakeRedis` | `_fakeredis_sync_instance` |

Both functions: try real Redis → test connection → fall back to fakeredis if unreachable.

### Multi-Droplet Behavior

| Component | Real Redis | Fakeredis (per-droplet) |
|-----------|-----------|------------------------|
| Audit/Metering/Metrics | Shared queue, one consumer | Per-droplet queue + worker, all save to same DB ✅ |
| Rate limiter | Global limits | Per-droplet limits (N× more lenient) |
| Idempotency | Global dedup | Per-droplet dedup (duplicates possible across droplets) |
| Cache | Shared, invalidation propagates | **Disabled in prod** (`NoOpCache`) — no stale data risk |
| Job queue | Shared, exactly-once delivery | Per-droplet, possible duplicates |

### Cache Behavior by Environment

| Environment | Real Redis | Cache Backend |
|-------------|-----------|---------------|
| Dev | ✅ | `RedisCache` — shared, full features |
| Dev | ❌ | `InMemoryCache` — per-process, good enough |
| Prod | ✅ | `RedisCache` — shared, invalidation across droplets |
| Prod | ❌ | `NoOpCache` — **disabled**, every call hits DB |

## Background Jobs

Jobs are enqueued to Redis and processed by workers in your FastAPI processes.

### How It Works

1. **Enqueue**: `get_job_client().enqueue("task_name", data)` pushes to Redis
2. **Dequeue**: Workers use atomic `RPOPLPUSH` - each job claimed by exactly one worker
3. **Process**: Handler runs with `(data, ctx, db)` - database connection included
4. **Retry**: Failed jobs retry up to 3 times with exponential backoff
5. **Dead Letter**: After max retries, jobs move to dead letter queue

### Scaling

With multiple uvicorn workers or server instances, all workers cooperate to process the queue. More instances = faster processing. Redis atomic operations ensure each job is processed exactly once - no duplicates, no locks needed.

### Handler Signature

```python
async def my_handler(data: dict, ctx: JobContext, db) -> Any:
    """
    data: The dict you enqueued
    ctx:  Job metadata (job_id, attempt, max_attempts, user_id, etc.)
    db:   Database connection (ready to use)
    """
    pass
```

### Example

```python
from app_kernel import get_job_client, JobContext

# Define handler
async def send_email(data, ctx, db):
    # db is ready to use - no need to acquire connection
    user = await db.find_entity("users", data["user_id"])
    
    await smtp.send(
        to=data["to"],
        subject=data["subject"],
        body=data["body"],
    )
    
    # Log attempt info if needed
    if ctx.attempt > 1:
        print(f"Retry {ctx.attempt} of {ctx.max_attempts}")
    
    return {"sent": True}

# Register in create_service
app = create_service(
    ...
    tasks={"send_email": send_email},
)

# Enqueue from routes
@router.post("/notify")
async def notify(
    user: UserIdentity = Depends(get_current_user),
):
    client = get_job_client()
    await client.enqueue("send_email", {
        "user_id": user.id,
        "to": "user@example.com",
        "subject": "Hello",
        "body": "World",
    })
    return {"queued": True}
```

### JobContext Fields

| Field | Type | Description |
|-------|------|-------------|
| `job_id` | `str` | Unique job identifier |
| `task_name` | `str` | Name of the task |
| `attempt` | `int` | Current attempt (1, 2, 3...) |
| `max_attempts` | `int` | Maximum attempts (default: 3) |
| `enqueued_at` | `datetime` | When job was enqueued |
| `started_at` | `datetime` | When this attempt started |
| `user_id` | `str` | Optional user who triggered job |
| `metadata` | `dict` | Additional metadata |

## Rate Limiting

Global middleware with three tiers:

| User Type | Default Limit |
|-----------|---------------|
| Anonymous | 30 requests/minute |
| Authenticated | 120 requests/minute |
| Admin | 600 requests/minute |

Exceeding returns `429 Too Many Requests` with `Retry-After` header.

### Override Per-Route

```python
from app_kernel import rate_limit, no_rate_limit

@router.post("/expensive")
@rate_limit(10)  # Only 10 requests/minute
async def expensive_op():
    ...

@router.post("/webhook")
@no_rate_limit  # Exempt (e.g., external webhooks)
async def webhook():
    ...
```

## Idempotency

Prevent duplicate execution of dangerous operations (emails, webhooks, one-shot actions).

```python
from app_kernel import idempotent

@router.post("/send-welcome-email")
@idempotent
async def send_welcome_email(req: EmailRequest, user = Depends(get_current_user)):
    return await send_email(req.to, "Welcome!", ...)
```

**How it works:**
- Key = `user_id + endpoint + body_hash`
- TTL = 1 year
- Duplicate request → returns cached response, handler NOT called
- Response header: `X-Idempotency-Replayed: true`

**⚠️ USE WITH CAUTION - Only for truly one-shot operations:**

| ✓ Good use | ✗ Bad use |
|------------|-----------|
| Send welcome email | Purchase (user may buy same item twice!) |
| Process webhook | Search |
| One-time setup | Update profile |
| Password reset email | Any operation user may repeat intentionally |

**Example of WRONG usage:**
```python
@router.post("/purchase")
@idempotent  # ❌ WRONG! User can't buy same product twice!
async def purchase(req: PurchaseRequest):
    ...
```

User buys 5 candles Monday, tries to buy 5 more Friday → gets Monday's cached response. **Bad!**

**For purchases/payments:** Use order IDs + Stripe's built-in idempotency, not this decorator.

## Caching

Cache is environment-aware: uses Redis in prod (shared across droplets with proper invalidation), in-memory in dev, and **disabled** in prod when Redis is unavailable (to avoid stale data across droplets). See [Cache Behavior by Environment](#cache-behavior-by-environment).

```python
from app_kernel import cached

@cached(ttl=300, key="user:{user_id}")
async def get_user(user_id: str, db):
    return await db.find_entity("users", user_id)
```

### Invalidation

```python
# Invalidate specific key
await get_user.invalidate(user_id="123")

# Or use cache client directly
from app_kernel import get_cache
await get_cache().delete("user:123")

# Pattern delete (e.g., after bulk update)
await get_cache().delete_pattern("user:*")

# Get or compute
result = await get_cache().get_or_set(
    "expensive:key",
    factory=compute_expensive_thing,
    ttl=300,
)
```

## Distributed Locks

Redis-backed distributed locks for coordinating across multiple workers/processes. Uses atomic Lua scripts to prevent race conditions.

```python
from app_kernel import acquire_lock, release_lock, renew_lock, auto_renew_lock

# Acquire — returns lock_id (UUID) or None if already locked
lock_id = await acquire_lock("deploy:svc-123:prod", ttl=300, holder="user-456")
if not lock_id:
    raise Exception("Already locked by another process")

# For long operations, auto-renew in the background
renewer = await auto_renew_lock("deploy:svc-123:prod", lock_id, ttl=300, interval=120)
try:
    await do_long_deployment()
finally:
    renewer.cancel()
    await release_lock("deploy:svc-123:prod", lock_id)
```

| Function | Description |
|----------|-------------|
| `acquire_lock(key, ttl, holder)` | Acquire lock. Returns `lock_id` (UUID) or `None`. |
| `release_lock(key, lock_id)` | Release lock (atomic — only if you own it). |
| `renew_lock(key, lock_id, ttl)` | Extend lock TTL (atomic — only if you own it). |
| `auto_renew_lock(key, lock_id, ttl, interval)` | Background task that calls `renew_lock` periodically. Cancel when done. |

When Redis is unavailable, `acquire_lock` returns a UUID (no contention protection) and `release_lock` returns `True`. This is safe for single-process dev but means no real locking — production requires real Redis.

## Streaming

SSE streaming with Redis-backed concurrency limits. Prevents a single user from monopolizing server resources.

```python
from app_kernel import stream_lease, StreamLimitExceeded, get_active_streams

# In your SSE endpoint
@router.get("/stream")
async def stream_data(user: UserIdentity = Depends(get_current_user)):
    try:
        async with stream_lease(user.id) as lease:
            async def generate():
                yield "data: started\n\n"
                await do_work()
                yield "data: done\n\n"
            return StreamingResponse(generate(), media_type="text/event-stream")
    except StreamLimitExceeded:
        raise HTTPException(429, "Too many concurrent streams")

# Check active streams for a user
active = await get_active_streams(user_id)  # Returns count
```

Default: 5 concurrent streams per user, 180s lease TTL. Configure via `StreamingSettings`:

```python
KernelSettings(
    streaming=StreamingSettings(
        max_concurrent_per_user=3,
        lease_ttl_seconds=300,
    ),
)
```

## HTTP Client

Pooled async HTTP client with connection reuse across requests. Built on `httpx`.

```python
from app_kernel import http_client, HttpConfig

# Simple usage (uses default pool)
async with http_client("https://api.example.com") as client:
    resp = await client.get("/users")
    data = resp.json()

# With custom config
async with http_client("https://api.example.com", config=HttpConfig(
    timeout=30,
    max_connections=20,
    retries=3,
)) as client:
    resp = await client.post("/webhook", json={"event": "deploy"})
```

The client pool is shared per base URL — multiple callers reuse the same connection pool.

## Profiler

Simple timing utility for measuring code block performance.

```python
from app_kernel import Profiler, profiled_function

# Manual profiling
p = Profiler()
await do_something()
print(p.report("Step 1"))  # "Step 1: 12.34 ms"

p.start()  # Reset timer
await do_something_else()
print(p.elapsed())  # 5.67 (milliseconds)

# Decorator — logs timing automatically
@profiled_function()
async def my_handler(data):
    ...
# Logs: "my_handler completed in 45.2ms"
```

## Custom Environment Checks

Validation that runs at startup. In prod, failures prevent startup.

```python
def check_stripe_key(settings):
    """Returns (passed: bool, error_message: str)"""
    if not settings.stripe_secret_key:
        return False, "Stripe key required"
    return True, ""

app = create_service(
    ...
    env_checks=[check_stripe_key],
)
```

## Health Checks

Determine `/readyz` response. Used by load balancers.

```python
async def check_db() -> tuple[bool, str]:
    try:
        async with db_context() as db:
            await db.execute("SELECT 1")
        return True, "connected"
    except Exception as e:
        return False, str(e)

app = create_service(
    ...
    health_checks=[("database", check_db)],
)
```

- All pass → `200 OK`
- Any fail → `503 Service Unavailable`

## Admin Worker

Processes audit logs and usage metrics asynchronously. Runs embedded in your FastAPI processes.

### What It Does

- **Audit Logging**: Records entity changes for compliance
- **Usage Metering**: Tracks API calls per user/workspace
- **Request Metrics**: Stores timing and error data

### Scaling

The admin worker runs in all your FastAPI instances. With multiple instances, they cooperate to process the queue faster. Redis atomic operations ensure each event is handled exactly once.

### Standalone Mode (Optional)

For debugging or dedicated worker boxes:

```bash
python -m app_kernel.admin_worker \
    --redis-url redis://localhost:6379 \
    --database-url postgresql://...
```

## Backup & Restore

The kernel provides automatic backups and two restore strategies: **full CSV rollback** (primary) and **history-based revert** (lightweight undo).

### When Backups Happen

Backups are triggered automatically in two situations:

**On startup (before migrations)** — every time your app starts, the database lifecycle creates a full backup (CSV + native) before running any schema migrations. In a blue-green deployment, the new container starts → backup runs → migrations run → health check passes → nginx switches. The backup captures the exact state before any schema change.

**On schedule** — a background worker runs periodic backups on a cron schedule. The default is daily at 3pm UTC. Configure it via `create_service`:

```python
app = create_service(
    ...
    backup_schedule="0 15 * * *",   # default: daily 3pm UTC
    # backup_schedule="0 */6 * * *",  # every 6 hours
    # backup_schedule="30 2 * * 1",   # Mondays at 2:30am UTC
    # backup_schedule=None,           # disable scheduled backups
)
```

Standard 5-field cron format (minute, hour, day, month, weekday), evaluated in UTC. Supports `*`, `*/N`, comma-separated values, and ranges.

### Backup Contents

Each backup creates two things in `.data/backups/`:

- **CSV export** (`csv_YYYYMMDD_HHMMSS_<hash>/`) — one `.csv` per table (entity, history, and meta), human-readable, portable across database backends. Schema hash in the directory name links back to the migration that produced it.
- **Native dump** — pg_dump for Postgres, mysqldump for MySQL, file copy for SQLite. Used for full database recovery via CLI tools.

### Restore Options

**1. Full CSV rollback (recommended)**

The primary restore path. Drops everything, replays migrations up to the backup's schema hash, then imports CSV data. Works across table renames, schema changes, and even backend switches.

```
POST /api/v1/admin/db/restore/full
{
    "backup_name": "csv_20260210_140000_a1b2c3d4",
    "confirm": false
}
```

With `confirm: false` (the default), returns backup metadata and table list for review. Set `confirm: true` to execute. This is **destructive** — all current data is dropped before restore.

Best for: disaster recovery, rolling back bad migrations, restoring to a known-good state.

**2. CSV table import (additive)**

Import specific tables from a CSV backup into the current schema without dropping anything. Rows with matching IDs are overwritten; rows created after the backup are kept.

```
POST /api/v1/admin/db/restore/csv
{
    "backup_name": "csv_20260210_140000_a1b2c3d4",
    "table_names": ["products", "products_history"],
    "confirm": true
}
```

If `table_names` is omitted, all CSVs in the backup directory are imported. The schema must already match — this does not replay migrations.

Best for: selective table recovery when the schema hasn't changed.

**3. History-based revert (lightweight undo)**

Revert a single table to a point in time using its history table. No backup file needed.

```
POST /api/v1/admin/db/restore/revert
{
    "table_name": "products",
    "target_time": "2026-02-10T14:30:00Z",
    "confirm": false
}
```

Queries the history table for each row's state at `target_time`, upserts those values (preserving original timestamps), and soft-deletes rows created after the cutoff. History tables are never modified — a new version is appended as audit trail.

Limitations: only works on tables in the current `@entity` code, cannot cross table renames, and new columns added after `target_time` get their DEFAULT value or NULL.

Best for: quick undo of recent accidental changes on a single table.

**4. Native restore (manual)**

For Postgres and MySQL, restore the native dump using `pg_restore` or `mysql` CLI tools. For SQLite, stop the app and replace the database file. This is a full database replacement — use it when the other options aren't sufficient.

### Admin Endpoints

All admin database endpoints require admin authentication and live under `/api/v1/admin/db/`. All destructive operations require `confirm: true`.

**Visibility:**

- `GET /migrations` — list applied migrations with timestamps, hashes, and operation counts
- `GET /migrations/{hash}` — full migration detail (operations JSON, audit file path)
- `GET /backups` — list available CSV restore points on disk
- `GET /schema/orphans` — columns and tables in the database that don't correspond to any `@entity` class (cleanup candidates after removing or renaming fields)

**Actions:**

- `POST /backup` — trigger a backup immediately
- `POST /backfill` — manually run rename backfills (same logic that runs on startup, useful after restoring data)
- `POST /restore/full` — ⚠️ destructive full rollback: clear DB → replay migrations → import CSV
- `POST /restore/csv` — additive CSV import into current schema
- `POST /restore/revert` — lightweight single-table undo via history tables

### Rollback Workflow

If a deployment introduces a bad migration or corrupts data:

1. **Redeploy the previous version** — the old code starts, its `@entity` classes match the pre-migration schema. Extra columns from the new code are left in place (harmless).
2. **Check what happened** — use `GET /admin/db/migrations` to see what changed, `GET /admin/db/schema/orphans` to spot leftover columns.
3. **Restore data** — use `POST /restore/full` with the pre-migration backup name. This is the safest path — it replays migrations from scratch and imports the exact CSV snapshot.
4. **Run backfills** — if the old code has `renamed_from` fields, hit `POST /backfill` to re-copy data into the expected columns.

## Auto-Mounted Routes

### Health & Metrics

| Route | Description |
|-------|-------------|
| `GET /healthz` | Liveness probe |
| `GET /readyz` | Readiness (runs health_checks) |
| `GET /metrics` | Prometheus metrics |

### Auth (`/api/v1/auth`)

| Route | Description |
|-------|-------------|
| `POST /register` | Register new user (if allow_self_signup) |
| `POST /login` | Login, returns JWT |
| `POST /refresh` | Refresh access token |
| `GET /me` | Current user profile |
| `POST /change-password` | Change password |
| `POST /logout` | Logout (invalidate token) |

### OAuth (`/api/v1/auth/oauth`) — if `oauth_providers` configured

| Route | Description |
|-------|-------------|
| `GET /{provider}` | Start OAuth flow (redirect to provider) |
| `GET /{provider}/callback` | OAuth callback (exchange code for token) |
| `GET /accounts` | List linked OAuth accounts |
| `DELETE /{provider}` | Unlink OAuth provider |

### Workspaces & SaaS (`/api/v1`)

| Route | Description |
|-------|-------------|
| `POST /workspaces` | Create workspace |
| `GET /workspaces` | List user's workspaces |
| `GET /workspaces/{id}` | Get workspace |
| `PATCH /workspaces/{id}` | Update workspace |
| `DELETE /workspaces/{id}` | Delete workspace |
| `GET /workspaces/{id}/members` | List members |
| `PATCH /workspaces/{id}/members/{user_id}` | Update member role |
| `DELETE /workspaces/{id}/members/{user_id}` | Remove member |
| `DELETE /workspaces/{id}/leave` | Leave workspace |
| `POST /workspaces/{id}/invites` | Create invite |
| `GET /workspaces/{id}/invites` | List invites |
| `DELETE /workspaces/{id}/invites/{invite_id}` | Revoke invite |
| `POST /invites/accept` | Accept invite |
| `GET /invites/pending` | List pending invites |
| `POST /workspaces/{id}/projects` | Create project |
| `GET /workspaces/{id}/projects` | List projects |
| `GET /workspaces/{id}/projects/{project_id}` | Get project |
| `PATCH /workspaces/{id}/projects/{project_id}` | Update project |
| `DELETE /workspaces/{id}/projects/{project_id}` | Delete project |

### API Keys (`/api/v1/api-keys`)

| Route | Description |
|-------|-------------|
| `POST /` | Create API key |
| `GET /` | List API keys |
| `GET /{key_id}` | Get API key |
| `DELETE /{key_id}` | Revoke API key |

### Webhooks (`/api/v1/webhooks`)

| Route | Description |
|-------|-------------|
| `POST /` | Create webhook |
| `GET /` | List webhooks |
| `GET /{webhook_id}` | Get webhook (with secret) |
| `PATCH /{webhook_id}` | Update webhook |
| `DELETE /{webhook_id}` | Delete webhook |
| `GET /{webhook_id}/deliveries` | Delivery log |
| `POST /{webhook_id}/test` | Send test event |

### Feature Flags (`/api/v1/flags`)

| Route | Description |
|-------|-------------|
| `POST /` | Create flag |
| `GET /` | List flags |
| `GET /{name}` | Get flag |
| `DELETE /{name}` | Delete flag |
| `GET /{name}/check` | Evaluate flag for current user |

### Usage Metering (`/api/v1/usage`)

| Route | Description |
|-------|-------------|
| `GET /` | Current user's usage (or any user for admin via `?user_id=`) |
| `GET /workspace/{id}` | Workspace usage |
| `GET /quota/{metric}` | Check quota status |

### Tasks (`/api/v1`)

| Route | Description |
|-------|-------------|
| `GET /tasks/{id}/status` | Check task status |
| `POST /tasks/{id}/cancel` | Cancel SSE task |

### Jobs (`/api/v1/jobs`)

| Route | Description |
|-------|-------------|
| `GET /` | List jobs |
| `GET /{job_id}` | Get job status |
| `POST /{job_id}/cancel` | Cancel job |

### Admin — Audit (`/api/v1/admin/audit`)

| Route | Description |
|-------|-------------|
| `GET /` | Query audit logs |
| `GET /entity/{type}/{id}` | Entity audit history |

### Admin — Action Replay (`/api/v1/admin`)

Captures frontend user action buffers on error for bug diagnosis. The frontend auto-POSTs a circular buffer of the last 25 actions (API calls, navigation, clicks) when a JS error or 5xx response occurs. Auth is optional on save (errors can happen before login).

Companion frontend: `actionLog` hook in `@myorg/ui` — configure with `actionLog.configure({ saveUrl: '/api/v1/admin/action-replay' })`.

| Route | Description |
|-------|-------------|
| `POST /action-replay` | Save replay (auth optional, auto-called by frontend) |
| `GET /action-replays` | List replays (admin) |
| `GET /action-replays/{id}` | Full replay with action log (admin) |
| `PATCH /action-replays/{id}/resolve` | Mark resolved (admin) |

### Admin — Usage (`/api/v1/admin/usage`)

| Route | Description |
|-------|-------------|
| `GET /user/{id}` | Specific user's usage |
| `GET /endpoints` | Usage by endpoint |

### Admin — Request Metrics (`/api/v1/admin/metrics`)

| Route | Description |
|-------|-------------|
| `GET /` | Request metrics list |
| `GET /stats` | Aggregated request stats |
| `GET /slow` | Slow requests (>1s) |
| `GET /errors` | Error requests (4xx/5xx) |

### Admin — Database (`/api/v1/admin/db`)

| Route | Description |
|-------|-------------|
| `GET /migrations` | List applied schema migrations |
| `GET /migrations/{hash}` | Migration detail (operations, audit file) |
| `GET /backups` | List CSV restore points on disk |
| `GET /schema/orphans` | Orphaned columns/tables in DB |
| `POST /backup` | Trigger backup now |
| `POST /backfill` | Run rename backfills |
| `POST /restore/full` | ⚠️ Full rollback: clear DB → replay migrations → import CSV |
| `POST /restore/csv` | Additive CSV import into current schema |
| `POST /restore/revert` | Single-table undo via history tables |

## API Reference

### Service Creation

| Export | Description |
|--------|-------------|
| `create_service(name, ...)` | Create a production-ready FastAPI app with all kernel features. |
| `quick_service(name, routers)` | Zero-config service for prototyping (SQLite + fakeredis). |
| `ServiceConfig` | Configuration dataclass for `create_service`. Has `from_env()` classmethod. |
| `init_app_kernel(app, settings, ...)` | Low-level init — attach kernel to an existing FastAPI app. |
| `get_kernel(app)` | Get `KernelRuntime` from an initialized FastAPI app. |
| `KernelRuntime` | Runtime state: `.logger`, `.metrics`, `.audit`, `.settings`, `.http_client()`. |

### Settings (all frozen dataclasses)

| Export | Description |
|--------|-------------|
| `KernelSettings` | Top-level settings container (composes all sub-settings). |
| `AuthSettings` | `token_secret`, `access_token_expires_minutes`, `refresh_token_expires_days`. |
| `RedisSettings` | `url`, `key_prefix`, `max_connections`, timeouts. |
| `StreamingSettings` | `max_concurrent_per_user`, `lease_ttl_seconds`. |
| `JobSettings` | `worker_count`, `thread_pool_size`, `work_timeout`, `max_attempts`. |
| `ObservabilitySettings` | `service_name`, `log_level`, `request_metrics_enabled`. |
| `TracingSettings` | `enabled`, `exclude_paths`, `sample_rate`, `save_threshold_ms`. |
| `ReliabilitySettings` | Rate limit RPMs, idempotency TTL. |
| `FeatureSettings` | Toggle auto-mounted routes, auth mode, signup, metrics protection. |
| `CorsSettings` | `allow_origins`, `allow_credentials`, `allow_methods`, `allow_headers`. |
| `SecuritySettings` | Toggle request ID, security headers, logging, error handling, max body size. |

### Auth

| Export | Type | Description |
|--------|------|-------------|
| `get_current_user` | Dependency | Require auth, return `UserIdentity`, raise 401 if missing. |
| `get_current_user_optional` | Dependency | Return `UserIdentity` or `None`. |
| `require_admin` | Dependency | Require admin role, raise 401/403. |
| `require_auth` | Dependency | Alias for `get_current_user`. |
| `get_request_context` | Dependency | Returns `RequestContext` with user + request metadata. |
| `UserIdentity` | Dataclass | `id`, `email`, `role`, `is_active`, `is_admin` (property). |
| `TokenPayload` | Dataclass | Decoded JWT: `sub`, `email`, `role`, `type`, `user_id` (property). |
| `RequestContext` | Dataclass | `user`, `request_id`, `ip_address`, `is_authenticated` (property). |
| `AuthError` | Exception | Raised for auth/token failures. |
| `UserStore` | Protocol | `get_by_username`, `get_by_id`, `create`, `update_password`. |
| `AuthServiceAdapter` | Class | **Deprecated.** Wraps `backend.auth.AuthService` to implement `UserStore`. Will be removed once RBAC is migrated into the kernel (see TODO below). |
| `create_auth_router(...)` | Factory | Create auth router with login/register/refresh/me/change-password. |

#### `app.state` — Auth Primitives for Custom Flows

After `init_app_kernel`, bootstrap exposes the user store and auth config on `app.state` so that app-specific routes (external provider auth, API key auth, magic links, etc.) can create/lookup users and issue tokens without importing kernel internals or writing to DB tables directly.

| Attribute | Type | Description |
|-----------|------|-------------|
| `app.state.user_store` | `UserStore` | The kernel's user store instance (`KernelUserStore` by default). |
| `app.state.auth_config` | `dict` | `{"token_secret", "access_token_expires_minutes", "refresh_token_expires_days"}` |

Example — external provider auth (e.g. DigitalOcean token, API key):

```python
from shared_libs.backend.app_kernel.auth import hash_password, create_access_token, create_refresh_token

async def authenticate_with_external_token(external_token: str, app):
    """Validate external token, then find/create user via kernel user_store."""
    # 1. Validate token with external provider (your logic)
    account = await validate_external_token(external_token)
    email = f"provider-{account['id']}@myapp.local"

    # 2. Find or create user via kernel user_store
    user_store = app.state.user_store
    auth_config = app.state.auth_config
    user = await user_store.get_by_username(email)

    if user:
        await user_store.update_password(user["id"], hash_password(external_token))
    else:
        user = await user_store.create(
            username=email, email=email,
            password_hash=hash_password(external_token),
        )

    # 3. Issue tokens via kernel auth utils
    access_token = create_access_token(
        user_id=user["id"], role=user.get("role", "user"),
        email=email, secret=auth_config["token_secret"],
        expires_minutes=auth_config["access_token_expires_minutes"],
    )
    return {"access_token": access_token, "user": user}
```

Access from any route via `request.app`:

```python
@router.post("/auth/my-provider")
async def my_provider_auth(req: MyRequest, request: Request):
    result = await authenticate_with_external_token(req.token, request.app)
    return result
```

> **TODO: RBAC migration.** Role-based access control (roles, permissions, resource-scoped assignments) currently lives in `shared_libs/backend/auth` as a separate module with its own `AuthService`, `DatabaseUserStore`, `DatabaseRoleStore`, and `auth_users` table. This creates confusion: two auth systems, two user tables, two sets of hashing/token utilities. The kernel auth handles user management, JWT tokens, and login/register — the only thing missing is RBAC. Plan: migrate `Role`, `RoleAssignment`, `has_permission`, `assign_role`, `revoke_role`, `require_permission` into the kernel, backed by `kernel_auth_roles` and `kernel_auth_role_assignments` tables. Once migrated, delete `shared_libs/backend/auth` entirely and remove `AuthServiceAdapter`.

### Database

| Export | Description |
|--------|-------------|
| `db_context()` | Async context manager for strict DB connections (enforces entity class usage). |

### Redis & Locks

| Export | Description |
|--------|-------------|
| `get_redis()` | Get shared async Redis client (or fakeredis). Returns `None` before init. |
| `get_sync_redis()` | Get shared sync Redis client (used by job queue). |
| `is_redis_fake()` | `True` if using fakeredis (in-memory, single-process). |
| `acquire_lock(key, ttl, holder)` | Acquire distributed lock. Returns `lock_id` or `None`. |
| `release_lock(key, lock_id)` | Release lock atomically (Lua script, ownership-verified). |
| `renew_lock(key, lock_id, ttl)` | Extend lock TTL atomically. |
| `auto_renew_lock(key, lock_id, ttl, interval)` | Background renewal task. Cancel when done. |

### Jobs

| Export | Description |
|--------|-------------|
| `JobRegistry` | Register task processors via `.register()` or `@registry.task()`. |
| `JobContext` | Passed to handlers: `job_id`, `task_name`, `attempt`, `max_attempts`, `user_id`. |
| `get_job_client()` | Get `JobClient` for enqueuing: `await client.enqueue("task", data)`. |
| `start_workers()` / `stop_workers()` | Start/stop job workers (for dedicated worker processes). |
| `run_worker(tasks, redis_url, ...)` | Run a standalone worker process with signal handling. |
| `create_jobs_router(...)` | Create router for job listing/status/cancel endpoints. |

### Streaming

| Export | Description |
|--------|-------------|
| `stream_lease(user_id)` | Async context manager — acquires a stream slot, raises `StreamLimitExceeded`. |
| `StreamLimitExceeded` | Exception when user exceeds concurrent stream limit. |
| `get_active_streams(user_id)` | Returns count of active streams for a user. |

### Caching & Reliability

| Export | Description |
|--------|-------------|
| `@cached(ttl, key)` | Decorator — cache function results in Redis/memory. |
| `@rate_limit(limit, window)` | Decorator — custom per-route rate limit. |
| `@no_rate_limit` | Decorator — exempt route from rate limiting. |
| `@idempotent` / `@idempotent(ttl=N)` | Decorator — prevent duplicate execution. |

### Observability

| Export | Description |
|--------|-------------|
| `get_logger()` | Get the kernel's structured logger. |
| `log_context(**kwargs)` | Context manager — adds fields to all logs within the block. |
| `get_metrics()` | Get the metrics collector (counters, histograms). |
| `get_audit()` | Get the audit logger for manual audit entries. |
| `RequestMetric` | Dataclass — rich metadata for a single HTTP request. |
| `RequestMetricsMiddleware` | Middleware — captures timing/status/user/geo per request. |
| `RequestMetricsStore` | DB store — save/query/aggregate request metrics. |
| `get_real_ip(request)` | Extract real client IP (handles reverse proxies). |
| `get_geo_from_headers(request)` | Extract country/city from Cloudflare headers. |
| `setup_request_metrics(app, ...)` | Attach `RequestMetricsMiddleware` to an app. |
| `create_request_metrics_router(...)` | Create admin router for metrics queries. |
| `get_traced_service_name()` | Get the service name used in tracing spans. |

### SaaS (Multi-Tenant)

| Export | Description |
|--------|-------------|
| `WorkspaceStore` | CRUD for workspaces: `create`, `get`, `list_for_user`, `update`, `delete`. |
| `MemberStore` | CRUD for members: `add`, `remove`, `update_role`, `is_member`, `is_admin`. |
| `InviteStore` | CRUD for invites: `create`, `accept`, `cancel`, `list_for_workspace`. |
| `require_workspace_member` | Dependency — require user is a member of the workspace in the URL. |
| `require_workspace_admin` | Dependency — require admin or owner role in the workspace. |
| `require_workspace_owner` | Dependency — require owner role in the workspace. |
| `create_saas_router(...)` | Create router with all workspace/member/invite endpoints. |

### Integrations

| Export | Description |
|--------|-------------|
| `send_email(to, subject, html, text, ...)` | Send transactional email via configured SMTP. |
| `is_email_configured()` | Check if email is configured. |
| `BillingService` | Stripe billing (optional — requires `billing` module). |
| `BillingConfig` | Billing configuration. |
| `StripeSync` | Sync products/prices to Stripe. |

### SSE Task Helpers

| Export | Description |
|--------|-------------|
| `TaskStream` | Full-featured stream context: logging, cancellation, SSE formatting. |
| `TaskCancelled` / `Cancelled` | Exception raised when a task is cancelled by user. |
| `create_tasks_router(...)` | Create router for task status/cancel endpoints. |
| `sse_event(event, data)` | Format a generic SSE event string. |
| `sse_task_id(task_id)` | Emit `task_id` SSE event for client cancellation. |
| `sse_log(message, level)` | Emit a log SSE event. |
| `sse_complete(success, task_id, error)` | Emit completion SSE event. |
| `sse_urls(endpoints, domain)` | Emit deployment/service URL SSE event. |

### Environment & Utilities

| Export | Description |
|--------|-------------|
| `get_env()` | Get current environment name (`prod`, `dev`, etc.). |
| `is_prod()` / `is_dev()` / `is_staging()` / `is_uat()` / `is_test()` | Environment checks. |
| `load_env_hierarchy(...)` | Load `.env` + `.env.{ENV}` files with directory inheritance. |
| `run_env_checks(settings, extra_checks)` | Run validation checks. Raises in prod on failure. |
| `check_database_url` / `check_redis_url` / `check_jwt_secret` / `check_cors_origins` / `check_email_config` | Built-in check functions (reusable in custom checks). |
| `EnvCheck` | Type alias for check functions: `(settings) -> (bool, str)`. |
| `Profiler` | Simple timer: `.start()`, `.elapsed()`, `.report(msg)`. |
| `profiled_function(is_entry)` | Decorator — logs function timing. |
| `http_client(base_url, config)` | Pooled async HTTP client context manager. |
| `HttpConfig` | HTTP client configuration (timeout, retries, max connections). |
| `CacheBustedStaticFiles` | StaticFiles subclass with smart cache headers (hash-aware). |
| `__version__` | Kernel version string. |
| `create_health_router(...)` | Create health/readiness router. |

---

## Advanced Features

### Feature Flags (A/B Testing)

Toggle features without deploying. Supports percentage rollout and targeting.

```python
from app_kernel.flags import flag_enabled, set_flag

# Check flag in route
@router.get("/dashboard")
async def dashboard(user = Depends(get_current_user)):
    if await flag_enabled("new_dashboard", user_id=user.id):
        return new_dashboard()
    return old_dashboard()

# Admin: Set flag with rollout
await set_flag(db, "new_dashboard",
    enabled=True,
    rollout_percent=10,           # 10% of users
    workspaces=["ws-123"],        # Specific workspaces always get it
    users=["user-456"],           # Specific users always get it
)

# Admin: List all flags
from app_kernel.flags import list_flags
flags = await list_flags(db)
```

### API Keys

Service-to-service authentication with scoped permissions.

```python
from app_kernel.api_keys import create_api_key, list_api_keys, revoke_api_key

# Create key (plaintext returned only once)
key_data = await create_api_key(db, user_id, workspace_id,
    name="CI/CD Pipeline",
    scopes=["deployments:write", "services:read"],
    expires_in_days=90,
)
# key_data = {"id": "...", "key": "sk_live_a1b2c3...", ...}

# Client uses: Authorization: Bearer sk_live_a1b2c3...

# In routes - accept API key OR JWT
from app_kernel.api_keys import get_combined_auth

@router.post("/deployments")
async def deploy(auth = Depends(get_combined_auth)):
    # auth.type = "api_key" or "user"
    # auth.user_id, auth.workspace_id, auth.scopes
    if "deployments:write" not in auth.scopes:
        raise HTTPException(403, "Missing scope")
    ...

# List keys (never returns plaintext)
keys = await list_api_keys(db, user_id)

# Revoke
await revoke_api_key(db, key_id, user_id)
```

### Webhooks

Notify external systems when events happen.

```python
from app_kernel.webhooks import create_webhook, trigger_webhook_event, list_webhooks

# Register webhook
webhook = await create_webhook(db, workspace_id,
    url="https://slack.com/webhook/xxx",
    secret="optional-hmac-secret",
)

# Trigger event (from your code)
await trigger_webhook_event(db, workspace_id,
    event="deployment.succeeded",
    data={"service": "api", "version": 42},
)
# Payload: {"event": "deployment.succeeded", "data": {...}, "timestamp": "..."}

# List webhooks
webhooks = await list_webhooks(db, workspace_id)
```

### Email

Send transactional emails via SMTP.

```python
from app_kernel.integrations import send_email, send_email_batch, is_email_configured

# Check if configured
if is_email_configured():
    # Send single email
    await send_email(
        to="user@example.com",
        subject="Welcome!",
        body="<h1>Hello</h1>",
        html=True,
    )
    
    # Batch send
    await send_email_batch([
        {"to": "a@example.com", "subject": "Hi A", "body": "..."},
        {"to": "b@example.com", "subject": "Hi B", "body": "..."},
    ])
```

Configure via `create_service`:
```python
app = create_service(
    ...
    smtp_url="smtp://user:pass@smtp.example.com:587",
    email_from="My App <noreply@myapp.com>",
)
```

### Billing (Stripe)

Full billing system with subscriptions, one-time purchases, and physical products. Requires `billing` module.

```python
from app_kernel import create_service, BillingService

async def seed_billing(db, billing: BillingService):
    """Seed products - gets (db, billing) injected. Stripe sync is automatic."""
    pro = await billing.create_product(db,
        name="Pro Plan",
        slug="pro",
        features=["api_access", "priority_support"],
        product_type="subscription",
    )
    await billing.create_price(db,
        product_id=pro["id"],
        amount_cents=1999,
        interval="month",
    )

app = create_service(
    name="my-api",
    stripe_secret_key="sk_live_...",
    stripe_webhook_secret="whsec_...",  # Optional
    seed_billing=seed_billing,
)
```

That's it! Router auto-mounted. Stripe sync happens automatically after seed_billing.

Auto-mounted billing routes:
```
# User routes
GET  /billing/products           - List products
GET  /billing/products/{slug}    - Get product
GET  /billing/subscription       - Current subscription
POST /billing/subscribe          - Create subscription checkout
POST /billing/purchase           - One-time purchase checkout
GET  /billing/orders             - List user's orders
POST /billing/portal             - Stripe customer portal
POST /billing/cancel             - Cancel subscription
GET  /billing/access/{feature}   - Check feature access
GET  /billing/purchased/{slug}   - Check if purchased
POST /billing/webhooks/stripe    - Stripe webhook

# Admin routes (requires admin role)
GET  /billing/admin/subscriptions     - All subscriptions
GET  /billing/admin/orders            - All orders
GET  /billing/admin/customers         - All customers
GET  /billing/admin/customers/{id}    - Customer with subscriptions/orders
GET  /billing/admin/revenue           - Revenue stats (MRR, counts)
```

### Usage Metering

Track API calls, tokens, or any metric for billing/quotas. User routes at `/api/v1/usage`, admin routes at `/api/v1/admin/usage`.

```python
from app_kernel.metering import track_usage, get_usage, check_quota

# Auto-tracked: every request is counted automatically via middleware

# User-facing routes:
#   GET /api/v1/usage                     - Current user's usage
#   GET /api/v1/usage?user_id=xyz         - Any user's usage (admin override)
#   GET /api/v1/usage/workspace/{id}      - Workspace usage
#   GET /api/v1/usage/quota/tokens?limit=100000  - Check quota

# Admin routes:
#   GET /api/v1/admin/usage/user/{id}     - Specific user's usage
#   GET /api/v1/admin/usage/endpoints     - Usage by endpoint

# Manual tracking (e.g., AI tokens)
await track_usage(redis, app="my-api",
    user_id=user.id,
    workspace_id=workspace_id,
    tokens=1500,
)

# Or query programmatically:
usage = await get_usage(db, app="my-api", period="2025-01")
# {"requests": 4521, "tokens": 125000}

# Check quota before expensive operation
if not await check_quota(db, app="my-api", workspace_id=ws_id, 
                         metric="tokens", limit=100000):
    raise HTTPException(402, "Token limit reached")
```

### Audit Logging

Automatic tracking of who changed what, when. Routes auto-mounted at `/api/v1/admin/audit` (admin only).

```python
from app_kernel.audit import get_audit_logs, get_entity_audit_history

# Auto-audit: save_entity/delete_entity calls are logged automatically

# Query via API:
#   GET /api/v1/admin/audit?entity=deployments&since=2025-01-01
#   GET /api/v1/admin/audit/entity/deployments/{id}

# Or query programmatically:
logs = await get_audit_logs(db,
    app="my-api",
    entity="deployments",
    since="2025-01-01",
)

# Get history for specific entity
history = await get_entity_audit_history(db, "deployments", deployment_id)
# [{"action": "create", "user_id": "...", "timestamp": "...", "changes": {...}}, ...]
```

### Request Metrics (Telemetry)

Every request is automatically tracked with timing, status, user, and geo data. Metrics are pushed to Redis and batch-saved by the admin worker (same pattern as audit and metering). Routes auto-mounted at `/api/v1/admin/metrics` (admin only).

```
# Query via API:
GET /api/v1/admin/metrics              - List recent requests
GET /api/v1/admin/metrics/stats        - Aggregated statistics  
GET /api/v1/admin/metrics/slow         - Slow requests (>1s)
GET /api/v1/admin/metrics/errors       - Error requests (4xx/5xx)

# Example: Get stats for last 24 hours
GET /api/v1/admin/metrics/stats?hours=24

# Example: Get slow requests on /api/v1/deploy
GET /api/v1/admin/metrics/slow?path=/api/v1/deploy&min_latency=500
```

Data collected per request:
- Path, method, status code
- Latency (ms)
- User ID (if authenticated)
- IP address, geo (country/city from headers)
- Timestamp

### SSE Task Streaming

Long-running operations with progress streaming, cancellation, and automatic cleanup.

```python
from app_kernel.tasks import TaskStream, TaskCancelled

async def deploy_service(request_data) -> AsyncIterator[str]:
    stream = TaskStream("deploy")
    try:
        yield stream.log("Building image...")  # Auto-sends task_id on first call
        await build_image()
        stream.check()  # Raises TaskCancelled if user cancelled
        
        yield stream.log("Pushing to registry...")
        await push_image()
        stream.check()
        
        yield stream.log("Deploying...")
        await deploy()
        
        yield stream.complete(True, result={"version": 42})
    except TaskCancelled:
        yield stream.complete(False, error="Cancelled by user")
    finally:
        stream.cleanup()

# Route returns SSE stream
@router.post("/deploy")
async def deploy(data: DeployRequest):
    return StreamingResponse(
        deploy_service(data),
        media_type="text/event-stream",
    )
```

Cancel endpoint is auto-mounted by the kernel: `POST /api/v1/tasks/{task_id}/cancel`

#### Cancel-Safe Long Operations

`stream.check()` only detects cancel when called. Long HTTP calls (cloud API provisioning, image builds, file transfers) can block for minutes with no check. Wrap them with `cancellable()` to poll for cancel every 0.5s:

```python
# Single call — responds to cancel within 0.5s instead of minutes
result = await stream.cancellable(provision_droplet(region='lon1'))

# Multiple concurrent calls
results = await stream.cancellable_gather(
    provision_droplet(region='lon1'),
    provision_droplet(region='lon1'),
    provision_droplet(region='lon1'),
)
```

The in-flight HTTP call continues in background on cancel — the caller's `except TaskCancelled` handler is responsible for cleanup (see below).

#### Cleanup on Cancel (`on_cancel`)

The hard part of cancellation is cleaning up resources that were already created (VMs, containers, files). Without this, cancelled operations leave orphans. `on_cancel()` registers undo callbacks as you create resources:

```python
async def deploy_service(do_token, ...) -> AsyncIterator[str]:
    stream = TaskStream("deploy")
    try:
        # Provision infrastructure
        droplets = await stream.cancellable(create_droplets(...))
        for d in droplets:
            stream.on_cancel(destroy_droplet, d['id'], do_token)  # ← register undo
        
        # Deploy to each server
        for d in droplets:
            await deploy_to(d, ...)
            stream.on_cancel(remove_container, d['ip'], name, do_token)  # ← register undo
        
        # Configure routing — past this point, everything is committed
        await configure_nginx(...)
        await setup_dns(...)
        
        yield stream.complete(True)
        # stream.cleanup() in finally clears all callbacks — nothing to undo
        
    except TaskCancelled:
        stream("Cancelled by user.")
        yield stream.log(level="warning")
        await stream.run_cleanups()  # ← runs all registered undos in LIFO order
        yield stream.complete(False, error="Cancelled by user")
    finally:
        stream.cleanup()
```

If a resource is committed before the task ends (no longer needs undo):

```python
handle = stream.on_cancel(delete_temp_file, path)
# ... upload succeeds, file is permanent now ...
handle.discard()  # remove from undo list
```

Key behaviors:

- Callbacks run in **reverse order** (LIFO) — last registered runs first, like a stack
- **Best-effort**: each callback runs independently, errors are logged but don't block remaining cleanups
- `stream.cleanup()` (in `finally`) clears all registered callbacks — successful tasks have nothing to undo
- Only the DB status update ("cancelled") and SSE completion event stay in the `except` block — everything else is handled by `run_cleanups()`

### Self-Testing

Run functional tests against your own API with SSE progress.

```python
from app_kernel.testing import TestReport, TestApiClient
from app_kernel.tasks import TaskStream

class MyClient(TestApiClient):
    async def create_widget(self, name):
        return await self.post("/widgets", json={"name": name})

async def run_functional_tests(base_url: str, auth_token: str):
    stream = TaskStream("functional-test")
    yield stream.task_id_event()
    
    api = MyClient(base_url, auth_token, outer_task_id=stream.task_id)
    report = TestReport()
    
    # Test 1
    stream("Testing widget creation...")
    yield stream.log()
    result = await api.create_widget("test")
    report.add_result("create_widget", "id" in result)
    
    stream(report.summary_line())
    yield stream.log()
    yield stream.complete(report.all_passed, report=report.to_dict())

# Register in create_service
app = create_service(
    ...
    test_runners=[run_functional_tests],
)
# → POST /test/functional-tests (admin only, SSE, cancellable)
```

### Access Control

Workspace membership and scope-based permissions.

```python
from app_kernel.access import require_workspace_member, require_scope, check_scope

# Require workspace membership
@router.get("/workspaces/{workspace_id}/data")
async def get_workspace_data(
    workspace_id: str,
    member = Depends(require_workspace_member),
):
    # member.role = "owner" | "admin" | "member"
    ...

# Require specific scope
@router.delete("/resources/{id}")
async def delete_resource(
    id: str,
    auth = Depends(require_scope("resources:delete")),
):
    ...

# Check scope programmatically
if await check_scope(auth, "admin:write"):
    # Allow admin action
    ...
```

---

## Class API Reference

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `ServiceConfig`

Service configuration with sensible defaults. Pass to `create_service(config=...)` or use `ServiceConfig.from_env()` to load from environment variables.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `@classmethod` | `from_env` | | `ServiceConfig` | Factory | Creates ServiceConfig from environment variables (`JWT_SECRET`, `DATABASE_URL`, `REDIS_URL`, `CORS_ORIGINS`, `DEBUG`, `LOG_LEVEL`, `RATE_LIMIT_RPM`, `SMTP_*`, `EMAIL_FROM`). |

</details>

<br>

<details>
<summary><strong>Fields</strong></summary>

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `jwt_secret` | `str` | `"dev-secret-change-me"` | JWT signing secret. |
| `jwt_expiry_hours` | `int` | `24` | JWT token expiry in hours. |
| `auth_enabled` | `bool` | `True` | Enable auth routes. |
| `allow_self_signup` | `bool` | `True` | Allow open registration. |
| `saas_enabled` | `bool` | `True` | Enable workspace/team routes. |
| `saas_invite_base_url` | `Optional[str]` | `None` | Base URL for invite links. |
| `oauth_providers` | `Dict[str, Dict[str, str]]` | `{}` | OAuth provider configs. |
| `redis_url` | `Optional[str]` | `None` | Redis connection string. |
| `redis_key_prefix` | `str` | `"queue:"` | Redis key prefix. |
| `database_url` | `Optional[str]` | `None` | Database connection string. |
| `admin_worker_embedded` | `bool` | `True` | Run admin worker in-process. |
| `admin_db_url` | `Optional[str]` | `None` | Separate DB URL for admin worker. |
| `cors_origins` | `List[str]` | `["*"]` | Allowed CORS origins. |
| `cors_credentials` | `bool` | `True` | Allow CORS credentials. |
| `rate_limit_enabled` | `bool` | `True` | Enable rate limiting middleware. |
| `rate_limit_requests` | `int` | `100` | Default rate limit RPM. |
| `rate_limit_window` | `int` | `60` | Rate limit window in seconds. |
| `max_concurrent_streams` | `int` | `3` | Max SSE streams per user. |
| `stream_lease_ttl` | `int` | `300` | Stream lease TTL in seconds. |
| `worker_count` | `int` | `4` | Background job worker count. |
| `job_max_attempts` | `int` | `3` | Max retry attempts per job. |
| `email_enabled` | `bool` | `False` | Enable email sending. |
| `email_provider` | `str` | `"smtp"` | Email provider type. |
| `email_from` | `Optional[str]` | `None` | Sender address. |
| `email_reply_to` | `Optional[str]` | `None` | Reply-to address. |
| `smtp_host` | `Optional[str]` | `None` | SMTP server host. |
| `smtp_port` | `int` | `587` | SMTP server port. |
| `smtp_user` | `Optional[str]` | `None` | SMTP username. |
| `smtp_password` | `Optional[str]` | `None` | SMTP password. |
| `smtp_use_tls` | `bool` | `True` | Use TLS for SMTP. |
| `debug` | `bool` | `False` | Enable debug mode. |
| `log_level` | `str` | `"INFO"` | Logging level. |
| `request_metrics_enabled` | `bool` | `False` | Enable per-request metrics collection. |
| `request_metrics_exclude_paths` | `List[str]` | `["/health", ...]` | Paths excluded from metrics. |
| `tracing_enabled` | `bool` | `True` | Enable request tracing. |
| `tracing_exclude_paths` | `List[str]` | `["/health", ...]` | Paths excluded from tracing. |
| `tracing_sample_rate` | `float` | `1.0` | Fraction of requests to trace. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `KernelSettings`

Complete configuration for app_kernel (frozen dataclass). Used with `init_app_kernel()` for low-level initialization.

<details>
<summary><strong>Fields</strong></summary>

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `redis` | `RedisSettings` | `RedisSettings()` | Redis connection and pool settings. |
| `streaming` | `StreamingSettings` | `StreamingSettings()` | SSE streaming limits. |
| `jobs` | `JobSettings` | `JobSettings()` | Background job worker settings. |
| `auth` | `AuthSettings` | `AuthSettings()` | Authentication settings. |
| `observability` | `ObservabilitySettings` | `ObservabilitySettings()` | Logging and metrics settings. |
| `tracing` | `TracingSettings` | `TracingSettings()` | Request tracing settings. |
| `reliability` | `ReliabilitySettings` | `ReliabilitySettings()` | Rate limiting and idempotency settings. |
| `features` | `FeatureSettings` | `FeatureSettings()` | Feature flags for auto-mounted routes. |
| `cors` | `CorsSettings` | `CorsSettings()` | CORS middleware settings. |
| `security` | `SecuritySettings` | `SecuritySettings()` | Security middleware settings. |
| `database_url` | `Optional[str]` | `None` | Database connection string. |
| `health_checks` | `Tuple[HealthCheckFn, ...]` | `()` | Custom health check functions. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `KernelRuntime`

Runtime state for an initialized kernel. Access via `get_kernel(app)`.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `async` | `http_client` | `base_url: str` | Context manager | HTTP | Get a pooled HTTP client for the given base URL. |

</details>

<br>

<details>
<summary><strong>Fields</strong></summary>

| Field | Type | Description |
|-------|------|-------------|
| `logger` | `KernelLogger` | Structured logger instance. |
| `metrics` | `MetricsCollector` | Prometheus-style metrics. |
| `audit` | `AuditLogger` | Audit log publisher. |
| `settings` | `KernelSettings` | Active kernel settings. |
| `redis_config` | `Any` | Redis configuration (sync client for jobs). |
| `job_registry` | `Optional[JobRegistry]` | Registered job handlers. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `AuthSettings`

Auth configuration (frozen dataclass).

<details>
<summary><strong>Fields</strong></summary>

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `token_secret` | `str` | `""` | JWT signing secret (32+ chars in prod). |
| `access_token_expires_minutes` | `int` | `15` | Access token TTL. |
| `refresh_token_expires_days` | `int` | `30` | Refresh token TTL. |
| `enabled` | `bool` | `True` | Enable auth routes. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `RedisSettings`

Redis connection settings (frozen dataclass).

<details>
<summary><strong>Fields</strong></summary>

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | `str` | `"redis://localhost:6379"` | Redis connection string. |
| `key_prefix` | `str` | `"queue:"` | Key prefix for job queues. |
| `max_connections` | `int` | `10` | Connection pool size. |
| `socket_timeout` | `float` | `5.0` | Socket timeout in seconds. |
| `socket_connect_timeout` | `float` | `5.0` | Connection timeout in seconds. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `StreamingSettings`

Streaming lifecycle settings (frozen dataclass).

<details>
<summary><strong>Fields</strong></summary>

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_concurrent_per_user` | `int` | `5` | Max concurrent SSE streams per user. |
| `lease_ttl_seconds` | `int` | `180` | Stream lease TTL before auto-release. |
| `lease_key_namespace` | `str` | `"stream_leases"` | Redis key namespace for leases. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `JobSettings`

Job queue settings (frozen dataclass).

<details>
<summary><strong>Fields</strong></summary>

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `worker_count` | `int` | `4` | Number of concurrent workers. |
| `thread_pool_size` | `int` | `8` | Thread pool for sync tasks. |
| `work_timeout` | `float` | `300.0` | Max seconds per job execution. |
| `max_attempts` | `int` | `3` | Retry attempts before dead-letter. |
| `retry_delays` | `Tuple[float, ...]` | `(1.0, 5.0, 30.0)` | Backoff delays between retries. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `ObservabilitySettings`

Logging and metrics settings (frozen dataclass).

<details>
<summary><strong>Fields</strong></summary>

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `service_name` | `str` | `"app"` | Service name in logs and metrics. |
| `log_level` | `str` | `"INFO"` | Logging level. |
| `log_dir` | `Optional[str]` | `None` | Directory for log files (None = stdout only). |
| `add_caller_info` | `bool` | `True` | Add caller file/line to log entries. |
| `request_metrics_enabled` | `bool` | `False` | Enable per-request metrics collection. |
| `request_metrics_exclude_paths` | `Tuple[str, ...]` | `("/health", ...)` | Paths excluded from metrics. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `TracingSettings`

Request tracing settings (frozen dataclass).

<details>
<summary><strong>Fields</strong></summary>

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `bool` | `True` | Enable tracing middleware. |
| `exclude_paths` | `Tuple[str, ...]` | `("/health", ...)` | Paths excluded from tracing. |
| `sample_rate` | `float` | `1.0` | Fraction of requests to trace (0.0–1.0). |
| `save_threshold_ms` | `float` | `0` | Only persist traces slower than this (0 = all). |
| `save_errors` | `bool` | `True` | Always persist error traces regardless of threshold. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `ReliabilitySettings`

Rate limiting and idempotency settings (frozen dataclass).

<details>
<summary><strong>Fields</strong></summary>

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `rate_limit_enabled` | `bool` | `True` | Enable rate limiting middleware. |
| `rate_limit_anonymous_rpm` | `int` | `30` | Requests/min for anonymous users. |
| `rate_limit_authenticated_rpm` | `int` | `120` | Requests/min for authenticated users. |
| `rate_limit_admin_rpm` | `int` | `600` | Requests/min for admin users. |
| `idempotency_enabled` | `bool` | `True` | Enable idempotency checking. |
| `idempotency_ttl_seconds` | `int` | `86400` | Idempotency key TTL (default 24h). |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `FeatureSettings`

Feature flags for auto-mounted kernel routers (frozen dataclass).

<details>
<summary><strong>Fields</strong></summary>

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enable_health_routes` | `bool` | `True` | Mount `/healthz` and `/readyz`. |
| `health_path` | `str` | `"/healthz"` | Liveness probe path. |
| `ready_path` | `str` | `"/readyz"` | Readiness probe path. |
| `enable_metrics` | `bool` | `True` | Mount `/metrics` (Prometheus). |
| `metrics_path` | `str` | `"/metrics"` | Metrics endpoint path. |
| `protect_metrics` | `Literal["admin", "internal", "none"]` | `"admin"` | Metrics endpoint protection level. |
| `enable_auth_routes` | `bool` | `True` | Mount auth routes (login/register/etc). |
| `auth_mode` | `Literal["local", "apikey", "external"]` | `"local"` | Authentication mode. |
| `allow_self_signup` | `bool` | `True` | Allow open registration. |
| `auth_prefix` | `str` | `"/auth"` | Auth routes prefix. |
| `enable_job_routes` | `bool` | `True` | Mount job management routes. |
| `job_routes_prefix` | `str` | `"/jobs"` | Job routes prefix. |
| `enable_audit_routes` | `bool` | `False` | Mount admin audit routes. |
| `audit_path` | `str` | `"/audit"` | Audit routes prefix. |
| `enable_saas_routes` | `bool` | `True` | Mount workspace/team routes. |
| `saas_prefix` | `str` | `"/workspaces"` | SaaS routes prefix. |
| `saas_invite_base_url` | `str` | `None` | Base URL for invite links. |
| `enable_test_routes` | `bool` | `False` | Mount self-test routes (admin only). |
| `kernel_prefix` | `str` | `""` | Global prefix for all kernel routes. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `CorsSettings`

CORS middleware configuration (frozen dataclass).

<details>
<summary><strong>Fields</strong></summary>

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `bool` | `True` | Enable CORS middleware. |
| `allow_origins` | `Tuple[str, ...]` | `("*",)` | Allowed origins. |
| `allow_credentials` | `bool` | `True` | Allow credentials (cookies, auth headers). |
| `allow_methods` | `Tuple[str, ...]` | `("*",)` | Allowed HTTP methods. |
| `allow_headers` | `Tuple[str, ...]` | `("*",)` | Allowed request headers. |
| `expose_headers` | `Tuple[str, ...]` | `("X-Runtime", "X-Request-ID")` | Headers exposed to browser. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `SecuritySettings`

Security middleware configuration (frozen dataclass).

<details>
<summary><strong>Fields</strong></summary>

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enable_request_id` | `bool` | `True` | Add `X-Request-ID` header. |
| `enable_security_headers` | `bool` | `True` | Add security headers (XSS, CSRF, etc). |
| `enable_request_logging` | `bool` | `True` | Log all requests with timing. |
| `enable_error_handling` | `bool` | `True` | Global error handling middleware. |
| `max_body_size` | `int` | `10485760` | Max request body size (10 MB). |
| `debug` | `bool` | `False` | Debug mode (verbose errors). |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `UserIdentity`

Core user identity for auth primitives (dataclass).

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `@property` | `is_admin` | | `bool` | Auth | Check if user has admin role. |
| | `to_dict` | | `dict` | Serialization | Convert to dictionary. |
| `@classmethod` | `from_dict` | `data: dict` | `UserIdentity` | Factory | Create from dictionary. |

</details>

<br>

<details>
<summary><strong>Fields</strong></summary>

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `str` | UUID | Unique user identifier. |
| `email` | `str` | `""` | User email address. |
| `role` | `str` | `"user"` | User role (`"user"`, `"admin"`). |
| `is_active` | `bool` | `True` | Whether account is active. |
| `created_at` | `datetime` | UTC now | Account creation timestamp. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `TokenPayload`

Decoded JWT token payload (dataclass).

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `@property` | `user_id` | | `str` | Auth | Alias for `sub`. |
| `@property` | `is_admin` | | `bool` | Auth | Check if role is admin. |
| `@property` | `is_refresh_token` | | `bool` | Auth | Check if type is refresh. |

</details>

<br>

<details>
<summary><strong>Fields</strong></summary>

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `sub` | `str` | required | Subject (user ID). |
| `email` | `str` | `""` | User email. |
| `role` | `str` | `"user"` | User role. |
| `type` | `str` | `"access"` | Token type (`"access"` or `"refresh"`). |
| `exp` | `Optional[datetime]` | `None` | Expiration time. |
| `iat` | `Optional[datetime]` | `None` | Issued-at time. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `RequestContext`

Request-scoped context containing user identity and metadata (dataclass).

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `@property` | `is_authenticated` | | `bool` | Auth | Whether a user is present. |
| `@property` | `user_id` | | `Optional[str]` | Auth | User ID if authenticated, else None. |
| `@property` | `is_admin` | | `bool` | Auth | Whether the user is an admin. |

</details>

<br>

<details>
<summary><strong>Fields</strong></summary>

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `user` | `Optional[UserIdentity]` | `None` | Authenticated user (if any). |
| `request_id` | `str` | UUID | Unique request identifier. |
| `timestamp` | `datetime` | UTC now | Request timestamp. |
| `ip_address` | `Optional[str]` | `None` | Client IP address. |
| `user_agent` | `Optional[str]` | `None` | Client user agent string. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `AuthError`

Raised for authentication/authorization failures. Inherits from `Exception`.

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `UserStore`

Protocol for user storage. Implement this to use custom user backends with `create_auth_router()`.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `async` | `get_by_username` | `username: str` | `Optional[dict]` | Query | Get user by username or email. |
| `async` | `get_by_id` | `user_id: str` | `Optional[dict]` | Query | Get user by ID. |
| `async` | `create` | `username: str`, `email: str`, `password_hash: str` | `dict` | Mutation | Create new user with pre-hashed password. |
| `async` | `update_password` | `user_id: str`, `password_hash: str` | `bool` | Mutation | Update user's password hash. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `AuthServiceAdapter`

Adapter that wraps `backend.auth.AuthService` to implement `UserStore` protocol.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `async` | `get_by_username` | `username: str` | `Optional[dict]` | Query | Get user by username (email). |
| `async` | `get_by_id` | `user_id: str` | `Optional[dict]` | Query | Get user by ID. |
| `async` | `create` | `username: str`, `email: str`, `password_hash: str` | `dict` | Mutation | Create new user with pre-hashed password. |
| `async` | `update_password` | `user_id: str`, `password_hash: str` | `bool` | Mutation | Update user's password. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `auth_service` | | Initialization | Wrap an AuthService instance. |
| | `_user_to_dict` | `user` | `dict` | Internal | Convert user entity to dict. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `Cache`

Cache facade with environment-aware backend selection. In dev: `InMemoryCache` or `RedisCache`. In prod without Redis: `NoOpCache` (disabled to prevent stale data across droplets).

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `@property` | `backend_type` | | `str` | Info | Return the backend type name for logging. |
| `async` | `get` | `key: str` | `Optional[Any]` | Read | Get value from cache. |
| `async` | `set` | `key: str`, `value: Any`, `ttl: Optional[int]` | `None` | Write | Set value with optional TTL (seconds). |
| `async` | `delete` | `key: str` | `bool` | Write | Delete a key. |
| `async` | `delete_pattern` | `pattern: str` | `int` | Write | Delete keys matching glob pattern (e.g., `"projects:*"`). |
| `async` | `clear` | | `None` | Write | Clear entire cache. |
| `async` | `exists` | `key: str` | `bool` | Read | Check if key exists. |
| `async` | `get_or_set` | `key: str`, `factory`, `ttl: Optional[int]` | `Any` | Read/Write | Get from cache or call factory. Single-flight per key (no stampede). |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `redis_client`, `prefix: str`, `is_fake: bool`, `is_prod: bool` | | Initialization | Select backend based on environment and Redis availability. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `InMemoryCache`

Simple in-memory LRU cache (fallback when Redis unavailable). Evicts expired entries every 50 `set()` calls.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `async` | `get` | `key: str` | `Optional[Any]` | Read | Get value, returns None if expired. |
| `async` | `set` | `key: str`, `value: Any`, `ttl: Optional[int]` | `None` | Write | Set value with LRU eviction at capacity. |
| `async` | `delete` | `key: str` | `bool` | Write | Delete a key. |
| `async` | `delete_pattern` | `pattern: str` | `int` | Write | Delete keys matching wildcard pattern. |
| `async` | `clear` | | `None` | Write | Clear all entries. |
| `async` | `exists` | `key: str` | `bool` | Read | Check if key exists and is not expired. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `max_size: int = 1000` | | Initialization | Create cache with max entry count. |
| | `_evict_expired` | | `None` | Internal | Remove all expired entries. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `RedisCache`

Async Redis-backed cache.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `async` | `get` | `key: str` | `Optional[Any]` | Read | Get value from Redis. |
| `async` | `set` | `key: str`, `value: Any`, `ttl: Optional[int]` | `None` | Write | Set value in Redis with optional TTL. |
| `async` | `delete` | `key: str` | `bool` | Write | Delete a key. |
| `async` | `delete_pattern` | `pattern: str` | `int` | Write | Delete keys matching pattern via SCAN. |
| `async` | `clear` | | `None` | Write | Clear all cache keys (by prefix). |
| `async` | `exists` | `key: str` | `bool` | Read | Check if key exists. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `redis_client`, `prefix: str` | | Initialization | Wrap a Redis client with key prefix. |
| | `_key` | `key: str` | `str` | Internal | Prefix a cache key. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `NoOpCache`

Cache that does nothing. Used in prod when real Redis is unavailable to prevent stale data across droplets.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `async` | `get` | `key: str` | `None` | Read | Always returns None. |
| `async` | `set` | `key: str`, `value: Any`, `ttl: Optional[int]` | `None` | Write | No-op. |
| `async` | `delete` | `key: str` | `False` | Write | No-op, returns False. |
| `async` | `delete_pattern` | `pattern: str` | `0` | Write | No-op, returns 0. |
| `async` | `clear` | | `None` | Write | No-op. |
| `async` | `exists` | `key: str` | `False` | Read | Always returns False. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `RateLimitConfig`

Configuration for rate limiting (dataclass).

<details>
<summary><strong>Fields</strong></summary>

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `anonymous_rpm` | `int` | `30` | Requests/min for anonymous users. |
| `authenticated_rpm` | `int` | `120` | Requests/min for authenticated users. |
| `admin_rpm` | `int` | `600` | Requests/min for admin users. |
| `key_prefix` | `str` | `"ratelimit:"` | Redis key prefix. |
| `exclude_paths` | `Set[str]` | `{"/health", ...}` | Paths exempt from rate limiting. |
| `exclude_prefixes` | `Set[str]` | `{"/static", "/_next"}` | Path prefixes exempt from rate limiting. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `RateLimiter`

Async Redis-backed sliding window rate limiter.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `async` | `check` | `identifier: str`, `limit: int`, `window: int` | `tuple[bool, int, int]` | Core | Check if request allowed. Returns `(allowed, remaining, retry_after)`. |
| `async` | `get_limit_for_request` | `request: Request`, `user: Optional[UserIdentity]` | `tuple[str, int]` | Core | Get rate limit key and limit for a request (considers per-route overrides). |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `redis_client`, `config: Optional[RateLimitConfig]` | | Initialization | Create rate limiter with Redis backend. |
| | `_get_key` | `identifier: str` | `str` | Internal | Build Redis key with prefix. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `IdempotencyChecker`

Manual idempotency checker for complex cases where the `@idempotent` decorator doesn't fit.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `async` | `check` | `key: str` | `tuple[bool, Optional[Any]]` | Read | Check if operation was already performed. Returns `(was_seen, cached_result)`. |
| `async` | `set` | `key: str`, `result: Any`, `ttl: Optional[int]` | | Write | Store result for idempotency key. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `redis_client`, `ttl: int` | | Initialization | Create checker with default TTL. |

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
| | `register` | `name: str`, `processor: ProcessorFunc` | `None` | Registration | Register a task processor. |
| | `task` | `name: str` | `Callable` | Registration | Decorator for registering a task processor. |
| | `get` | `name: str` | `Optional[ProcessorFunc]` | Query | Get a processor by name. |
| | `get_metadata` | `name: str` | `Optional[Dict[str, Any]]` | Query | Get metadata for a task. |
| | `has` | `name: str` | `bool` | Query | Check if a task is registered. |
| `@property` | `tasks` | | `Dict[str, ProcessorFunc]` | Query | Get all registered tasks (read-only view). |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `JobContext`

Context/metadata passed to job handlers (dataclass).

<details>
<summary><strong>Fields</strong></summary>

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `job_id` | `str` | required | Unique job identifier. |
| `task_name` | `str` | required | Name of the task. |
| `attempt` | `int` | `1` | Current attempt number (1, 2, 3...). |
| `max_attempts` | `int` | `3` | Maximum attempts before dead-letter. |
| `enqueued_at` | `Optional[datetime]` | `None` | When job was enqueued. |
| `started_at` | `datetime` | UTC now | When this attempt started. |
| `user_id` | `Optional[str]` | `None` | User who triggered the job. |
| `metadata` | `Dict[str, Any]` | `{}` | Additional metadata. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `TaskStream`

Streaming task context with built-in cancel support and SSE formatting.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `register` | `task_id: str` | | Lifecycle | Register for cancellation (deferred pattern). |
| | `cleanup` | | | Lifecycle | Remove cancel registration and clear callbacks. Call in `finally`. |
| | `check` | | | Cancel | Check if cancelled, raise `TaskCancelled` if so. |
| `@property` | `is_cancelled` | | `bool` | Cancel | Check if cancelled (without raising). |
| `async` | `cancellable` | `coro: Awaitable`, `interval: float = 0.5` | `Any` | Cancel | Await a coroutine while polling for cancellation. Use for long HTTP calls. |
| `async` | `cancellable_gather` | `*coros: Awaitable`, `interval: float = 0.5` | `List[Any]` | Cancel | Like `asyncio.gather()` but polls for cancellation while waiting. |
| | `on_cancel` | `fn: Callable`, `*args`, `**kwargs` | `_CancelHandle` | Cancel | Register a cleanup callback (LIFO). Call `handle.discard()` to remove. |
| `async` | `run_cleanups` | | | Cancel | Run all registered cancel callbacks in reverse order. Call in `except TaskCancelled`. |
| | `task_id_event` | | `str` | SSE | Emit `task_id` SSE event (usually auto-sent by first `log()` call). |
| | `log` | `message: str`, `level: str = "info"` | `str` | SSE | Emit a log message as an SSE event. |
| | `complete` | `success: bool`, `error: str = ""` | `str` | SSE | Emit completion SSE event. |
| | `event` | `event_name: str`, `data: dict` | `str` | SSE | Emit a custom SSE event. |
| | `flush` | | `str` | SSE | Return all logs joined by newlines. |
| `@property` | `last` | | `str` | SSE | Return the last log message. |
| `@property` | `logs` | | `List[str]` | SSE | Direct access to log list (for polling loops). |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `prefix: str`, `task_id: str = ""`, `register: bool = True` | | Initialization | Create stream context with auto-generated task ID. |
| | `__call__` | `msg: str` | | Shorthand | Set message for next `log()` call. |
| `async` | `__aenter__` | | | Context | Async context manager entry (registers). |
| `async` | `__aexit__` | `exc_type, exc_val, exc_tb` | | Context | Async context manager exit (cleanup). |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `_CancelHandle`

Handle returned by `TaskStream.on_cancel()`. Call `discard()` when the resource is committed and no longer needs cleanup on cancel.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `discard` | | | Lifecycle | Remove this cleanup from the undo list — resource was committed successfully. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `TaskCancelled`

Raised when a task is cancelled by user. Inherits from `Exception`. Alias: `Cancelled`.

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `StreamLimitExceeded`

Raised when a user exceeds their concurrent stream limit. Inherits from `Exception`.

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `WorkspaceStore`

CRUD operations for workspaces.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `async` | `create` | `name: str`, `owner_id: str`, `slug: str = ""`, `is_personal: bool = False`, `settings: Dict = {}` | `Dict[str, Any]` | Mutation | Create a new workspace and add owner as member. |
| `async` | `get` | `workspace_id: str` | `Optional[Dict[str, Any]]` | Query | Get workspace by ID. |
| `async` | `get_by_slug` | `slug: str` | `Optional[Dict[str, Any]]` | Query | Get workspace by slug. |
| `async` | `list_for_user` | `user_id: str` | `List[Dict[str, Any]]` | Query | List all workspaces user is a member of. |
| `async` | `get_personal_workspace` | `user_id: str` | `Optional[Dict[str, Any]]` | Query | Get user's personal workspace. |
| `async` | `update` | `workspace_id: str`, `updates: Dict[str, Any]` | `Optional[Dict[str, Any]]` | Mutation | Update workspace. |
| `async` | `delete` | `workspace_id: str` | `bool` | Mutation | Delete workspace and all members/invites. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `conn` | | Initialization | Wrap a database connection. |
| | `_generate_slug` | `name: str` | `str` | Internal | Generate URL-safe slug from workspace name. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `MemberStore`

CRUD operations for workspace members.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `async` | `add` | `workspace_id: str`, `user_id: str`, `role: str`, `invited_by: str` | `Dict[str, Any]` | Mutation | Add a member to workspace. |
| `async` | `get` | `workspace_id: str`, `user_id: str` | `Optional[Dict[str, Any]]` | Query | Get specific membership. |
| `async` | `list_for_workspace` | `workspace_id: str` | `List[Dict[str, Any]]` | Query | List all members of a workspace. |
| `async` | `update_role` | `workspace_id: str`, `user_id: str`, `role: str` | `Optional[Dict[str, Any]]` | Mutation | Update member's role. |
| `async` | `remove` | `workspace_id: str`, `user_id: str` | `bool` | Mutation | Remove member from workspace. |
| `async` | `is_member` | `workspace_id: str`, `user_id: str` | `bool` | Query | Check if user is a member. |
| `async` | `is_admin` | `workspace_id: str`, `user_id: str` | `bool` | Query | Check if user is admin or owner. |
| `async` | `is_owner` | `workspace_id: str`, `user_id: str` | `bool` | Query | Check if user is owner. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `conn` | | Initialization | Wrap a database connection. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `InviteStore`

CRUD operations for workspace invites.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `async` | `create` | `workspace_id: str`, `email: str`, `role: str`, `invited_by: str` | `Dict[str, Any]` | Mutation | Create a new invite (generates token). |
| `async` | `get` | `invite_id: str` | `Optional[Dict[str, Any]]` | Query | Get invite by ID. |
| `async` | `get_by_token` | `token: str` | `Optional[Dict[str, Any]]` | Query | Get invite by token. |
| `async` | `get_pending_for_email` | `workspace_id: str`, `email: str` | `Optional[Dict[str, Any]]` | Query | Get pending invite for email in workspace. |
| `async` | `list_for_workspace` | `workspace_id: str`, `status: str = "pending"` | `List[Dict[str, Any]]` | Query | List invites for workspace. |
| `async` | `list_for_email` | `email: str` | `List[Dict[str, Any]]` | Query | List pending invites for an email. |
| `async` | `accept` | `token: str`, `user_id: str` | `Optional[Dict[str, Any]]` | Mutation | Accept an invite (adds user as member). |
| `async` | `cancel` | `invite_id: str` | `bool` | Mutation | Cancel an invite. |
| `async` | `delete` | `invite_id: str` | `bool` | Mutation | Delete an invite. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `conn` | | Initialization | Wrap a database connection. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `RequestMetric`

Rich metadata for a single HTTP request (dataclass).

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `to_dict` | | `Dict[str, Any]` | Serialization | Convert to dictionary for storage. |

</details>

<br>

<details>
<summary><strong>Fields</strong></summary>

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `request_id` | `str` | required | Unique request identifier. |
| `method` | `str` | required | HTTP method. |
| `path` | `str` | required | Request path. |
| `query_params` | `Optional[str]` | `None` | Query string. |
| `status_code` | `int` | `0` | Response status code. |
| `error` | `Optional[str]` | `None` | Error message (if any). |
| `error_type` | `Optional[str]` | `None` | Error class name. |
| `server_latency_ms` | `float` | `0.0` | Server-side latency in milliseconds. |
| `client_ip` | `str` | `"unknown"` | Client IP address. |
| `user_agent` | `Optional[str]` | `None` | Client user agent. |
| `referer` | `Optional[str]` | `None` | HTTP referer header. |
| `user_id` | `Optional[str]` | `None` | Authenticated user ID. |
| `workspace_id` | `Optional[str]` | `None` | Workspace ID (if applicable). |
| `country` | `Optional[str]` | `None` | Country (from Cloudflare headers). |
| `city` | `Optional[str]` | `None` | City (from Cloudflare headers). |
| `continent` | `Optional[str]` | `None` | Continent code. |
| `timestamp` | `str` | `""` | ISO timestamp (auto-set). |
| `year` / `month` / `day` / `hour` | `int` | `0` | Time components (auto-set for partitioning). |
| `metadata` | `Dict[str, Any]` | `{}` | Additional metadata. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `RequestMetricsMiddleware`

Middleware that captures request metrics and pushes to Redis for async storage. Inherits from `BaseHTTPMiddleware`.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `async` | `dispatch` | `request: Request`, `call_next: Callable` | `Response` | Core | Capture timing, status, user, geo and push metric to Redis. |

</details>

<br>

<details>
<summary><strong>Private/Internal Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `app: ASGIApp`, `redis_client = None`, `redis_client_factory = None`, `exclude_paths: Optional[set] = None`, `sensitive_params: Optional[set] = None` | | Initialization | Create middleware. Accepts either a Redis client or a lazy factory. |
| | `_get_redis` | | Redis client | Internal | Lazy Redis resolution — factory called on first use. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `RequestMetricsStore`

Database store for request metrics (hot data).

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `@classmethod`, `async` | `init_schema` | `db` | | Setup | Initialize the `kernel_request_metrics` table. |
| `async` | `save` | `metric: Dict[str, Any]` | `str` | Write | Save a request metric. Returns ID. |
| `async` | `get_recent` | `limit: int`, `offset: int`, `path_prefix: Optional[str]`, `status_code: Optional[int]`, `user_id: Optional[str]`, `min_latency_ms: Optional[float]` | `List[Dict]` | Query | Get recent metrics with optional filters. |
| `async` | `get_stats` | `hours: int`, `path_prefix: Optional[str]` | `Dict[str, Any]` | Query | Get aggregated statistics (avg latency, error rates, etc). |
| `async` | `cleanup_old` | `days: int` | `int` | Maintenance | Delete metrics older than N days. Returns count deleted. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `Profiler`

Simple timer for code blocks.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `start` | | `None` | Timing | Reset the start time to current time. |
| | `elapsed` | | `float` | Timing | Return elapsed time in milliseconds since last `start()` or init. |
| | `report` | `msg: str` | `str` | Timing | Return formatted string, e.g. `"Step 1: 12.34 ms"`. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `HttpConfig`

HTTP client configuration for `http_client()`.

<details>
<summary><strong>Fields</strong></summary>

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `timeout` | `float` | `30.0` | Request timeout in seconds. |
| `max_connections` | `int` | `10` | Connection pool size per base URL. |
| `retries` | `int` | `0` | Number of retries on failure. |

</details>

<br>


</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### class `CacheBustedStaticFiles`

StaticFiles subclass with smart cache control headers. Inherits from `starlette.staticfiles.StaticFiles`.

- HTML files: `no-cache, no-store, must-revalidate` (+ CDN headers)
- Hashed assets (e.g. `main.a1b2c3d4.js`): `immutable, max-age=31536000` (1 year)
- Non-hashed assets: `max-age=3600, must-revalidate` (1 hour)

Usage: `app.mount("/", CacheBustedStaticFiles(directory="static", html=True), name="static")`

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">


### `create_action_replay_router`

Factory for frontend action replay routes. Companion to `actionLog` hook in `@myorg/ui`.

<details>
<summary><strong>Parameters</strong></summary>

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `get_current_user` | `Callable` | required | Auth dependency for admin routes |
| `get_current_user_optional` | `Callable` | `None` | Optional auth for save route (errors can happen before login) |
| `prefix` | `str` | `""` | Route prefix |
| `tags` | `List[str]` | `["action-replay"]` | OpenAPI tags |
| `is_admin` | `Callable` | `None` | `(user) -> bool`. Defaults to `role == "admin"` |

</details>

<br>

<details>
<summary><strong>Standalone Functions</strong></summary>

| Function | Args | Returns | Description |
|----------|------|---------|-------------|
| `save_replay` | `db`, `error_message`, `error_source`, `url`, `user_agent`, `replay_log`, `user_id`, `workspace_id` | `str` | Save a replay, returns ID. All args optional except `db`. |
| `list_replays` | `db`, `workspace_id=None`, `resolved=None`, `since=None`, `until=None`, `limit=50`, `offset=0` | `List[Dict]` | List replays (summary, no log body). |
| `get_replay` | `db`, `replay_id` | `Dict \| None` | Get full replay including parsed action log. |
| `resolve_replay` | `db`, `replay_id` | `bool` | Mark replay as resolved. |

</details>

<br>


</div>

## License

MIT