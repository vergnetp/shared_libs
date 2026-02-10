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

You get: Auth, SaaS (workspaces/teams), background jobs, rate limiting, caching, audit logging, health checks, metrics.

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

On startup, the kernel compares your `@entity` classes against the actual database schema. New tables and columns are created automatically. Renames and deletions are flagged with warnings — the migrator won't drop data without explicit opt-in. Migration scripts are persisted in `.data/migrations_audit/` for traceability. In production, a full backup (native + CSV) is taken before any migration runs, stored in `.data/backups/`.

Migrations are **forward-only**. There is no `migrate down` command. To rollback: redeploy the previous version of your code (with the old `@entity` classes) and restore from backup if needed. The pre-migration backup exists precisely for this — but be aware that data written between the migration and the rollback will be lost.

<!--
TODO: Migration rollback brainstorming

Current gap: rollback is manual (redeploy old code + restore backup). Problems:
- Old code on restart sees "extra" columns (added by new code) and warns but doesn't drop them → OK, harmless
- But if new code renamed a column, old code creates the old name again → now you have both → messy
- No way to rollback just the schema change without restoring data too
- deploy_api has its own backup/restore layer for user services — different concern, but could conflict if kernel migration + deploy rollback happen together

Ideas to explore:
1. Generate reversible migrations: for each "add column X", store "drop column X" as the down step. On rollback, apply downs in reverse. Risk: data loss on column drops.
2. Schema version pinning: tag each migration with app version. On startup, if app version < last migration version, refuse to start and log "rollback detected, restore backup first". At least prevents silent corruption.
3. Shadow columns: instead of dropping on rollback, mark columns as "orphaned" and ignore them. Old code works fine, new code can reclaim them later. Zero data loss but schema grows.
4. Point-in-time restore: combine CSV backup + entity history to reconstruct state at any timestamp. Heavy but complete.
5. Migration lock: prevent concurrent startups from racing on migrations (already partially handled by SQLite's write lock, but Postgres needs advisory locks).
6. Dry-run on deploy: before deploying new code, diff @entity classes against live schema and show what would change. Block deploy if destructive.
-->


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

All configuration is explicit - pass it to `create_service()`. Only `ENV` is read from environment (defaults to `prod`).

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

## Auto-Mounted Routes

| Route | Description |
|-------|-------------|
| `GET /healthz` | Liveness probe |
| `GET /readyz` | Readiness (runs health_checks) |
| `GET /metrics` | Prometheus metrics |
| `POST /api/v1/auth/register` | Register (if allow_self_signup) |
| `POST /api/v1/auth/login` | Login |
| `GET /api/v1/auth/me` | Current user |
| `* /api/v1/workspaces/*` | Workspace CRUD |
| `GET /api/v1/audit` | Query audit logs (admin) |
| `GET /api/v1/audit/entity/{type}/{id}` | Entity history (admin) |
| `GET /api/v1/usage` | Current user's usage (or any user for admin) |
| `GET /api/v1/usage/user/{id}` | Specific user's usage (admin) |
| `GET /api/v1/usage/workspace/{id}` | Workspace usage |
| `GET /api/v1/usage/endpoints` | Usage by endpoint |
| `GET /api/v1/usage/quota/{metric}` | Check quota status |
| `GET /api/v1/metrics/requests` | Request metrics list (admin) |
| `GET /api/v1/metrics/requests/stats` | Aggregated stats (admin) |
| `GET /api/v1/metrics/requests/slow` | Slow requests (admin) |
| `GET /api/v1/metrics/requests/errors` | Error requests (admin) |
| `POST /api/v1/tasks/{id}/cancel` | Cancel SSE task |

## API Reference

### Core

| Export | Description |
|--------|-------------|
| `create_service(...)` | Create FastAPI app |
| `get_job_client()` | Background job client |
| `db_context()` | Context manager for DB (batching) |
| `get_cache()` | Cache client |

### Auth

| Export | Description |
|--------|-------------|
| `get_current_user` | Require auth, return user |
| `get_current_user_optional` | Return user or None |
| `require_admin` | Require admin user |
| `UserIdentity` | User type |

### Decorators

| Decorator | Description |
|-----------|-------------|
| `@cached(ttl, key)` | Cache results |
| `@rate_limit(rpm)` | Custom rate limit |
| `@no_rate_limit` | Exempt from rate limit |

### Environment

| Function | Description |
|----------|-------------|
| `get_env()` | Get environment name |
| `is_prod()` | Check if production |
| `is_dev()` | Check if development |

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

Track API calls, tokens, or any metric for billing/quotas. Routes auto-mounted at `/api/v1/usage`.

```python
from app_kernel.metering import track_usage, get_usage, check_quota

# Auto-tracked: every request is counted automatically via middleware

# Query via API:
#   GET /api/v1/usage                     - Current user's usage
#   GET /api/v1/usage?user_id=xyz         - Any user's usage (admin)
#   GET /api/v1/usage/user/{id}           - Specific user (admin)
#   GET /api/v1/usage/workspace/{id}      - Workspace usage
#   GET /api/v1/usage/endpoints           - By endpoint
#   GET /api/v1/usage/quota/tokens?limit=100000  - Check quota

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

Automatic tracking of who changed what, when. Routes auto-mounted at `/api/v1/audit` (admin only).

```python
from app_kernel.audit import get_audit_logs, get_entity_audit_history

# Auto-audit: save_entity/delete_entity calls are logged automatically

# Query via API:
#   GET /api/v1/audit?entity=deployments&since=2025-01-01
#   GET /api/v1/audit/entity/deployments/{id}

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

Every request is automatically tracked with timing, status, user, and geo data. Metrics are pushed to Redis and batch-saved by the admin worker (same pattern as audit and metering). Routes auto-mounted at `/api/v1/metrics/requests` (admin only).

```
# Query via API:
GET /api/v1/metrics/requests              - List recent requests
GET /api/v1/metrics/requests/stats        - Aggregated statistics  
GET /api/v1/metrics/requests/slow         - Slow requests (>1s)
GET /api/v1/metrics/requests/errors       - Error requests (4xx/5xx)

# Example: Get stats for last 24 hours
GET /api/v1/metrics/requests/stats?hours=24

# Example: Get slow requests on /api/v1/deploy
GET /api/v1/metrics/requests/slow?path=/api/v1/deploy&min_latency=500
```

Data collected per request:
- Path, method, status code
- Latency (ms)
- User ID (if authenticated)
- IP address, geo (country/city from headers)
- Timestamp

### SSE Task Streaming

Long-running operations with progress streaming and cancellation.

```python
from app_kernel.tasks import TaskStream, TaskCancelled

async def deploy_service(request_data) -> AsyncIterator[str]:
    stream = TaskStream("deploy")
    try:
        yield stream.log("Building image...")  # Auto-sends task_id on first call
        await build_image()
        stream.check()  # Raises Cancelled if user cancelled
        
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

Client cancels via: `POST /tasks/{task_id}/cancel`

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

## License

MIT
