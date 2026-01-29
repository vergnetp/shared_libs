# app_kernel

A stable, reusable application kernel for backend services.

## Overview

`app_kernel` provides runtime infrastructure that can be reused across multiple backend services. It handles auth, database, jobs, and observability so you don't re-implement them every time.

**Philosophy:**
- **Kernel provides:** mechanisms + invariants
- **Apps provide:** meaning + business logic
- **Database is schemaless:** Auto-creates tables and columns as needed
- **Models are code:** Define dataclasses for validation and documentation (optional)

## What You Get For Free

| Feature | What It Does |
|---------|-------------|
| **JWT Auth** | Login, register, token refresh |
| **API Keys** | Service-to-service authentication |
| **Database** | Schemaless - auto-creates tables/columns |
| **Background Jobs** | Async task queue with retries |
| **Health Checks** | `/healthz`, `/readyz` endpoints |
| **Metrics** | Prometheus `/metrics` endpoint |
| **Rate Limiting** | Per-user request limits |
| **Request Tracking** | Request IDs, structured logging |
| **CORS** | Configurable origins |
| **SaaS** | Multi-tenant workspaces (optional) |
| **OAuth** | Google/GitHub login (optional) |
| **Feature Flags** | Toggle features without deploy |
| **Webhooks** | Notify external systems on events |
| **Usage Metering** | Track API calls, quotas, billing |
| **Audit Logging** | Who changed what, when |
| **Caching** | Redis-backed with in-memory fallback |

---

## Quick Start

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app_kernel import create_service, ServiceConfig, get_current_user
from app_kernel.db import db_connection


# Pydantic for request validation
class WidgetCreate(BaseModel):
    name: str
    color: str = "blue"


router = APIRouter(prefix="/widgets", tags=["widgets"])


@router.post("")
async def create_widget(data: WidgetCreate, user=Depends(get_current_user), db=Depends(db_connection)):
    # Auto-creates 'widgets' table, auto-generates id/timestamps
    # created_by/updated_by auto-tracked via context
    return await db.save_entity("widgets", {
        "name": data.name,
        "color": data.color,
    })


@router.get("")
async def list_widgets(user=Depends(get_current_user), db=Depends(db_connection)):
    return await db.find_entities("widgets", where_clause="[created_by] = ?", params=(user.id,))


@router.get("/{id}")
async def get_widget(id: str, db=Depends(db_connection)):
    widget = await db.get_entity("widgets", id)
    if not widget:
        raise HTTPException(404)
    return widget


app = create_service(
    name="widget_service",
    routers=[router],
    config=ServiceConfig.from_env(),
)
```

Run with:
```bash
JWT_SECRET=my-secret DATABASE_PATH=./data/app.db uvicorn main:app --reload
```

---

## Project Structure

```
my_service/
├── main.py                # FastAPI app entry point
├── config.py              # Service configuration (optional)
├── models.py              # Dataclass definitions (optional)
└── src/
    ├── routes/            # API endpoints
    └── workers/           # Background tasks
```

**No manifest. No code generation. No ORM.**

---

## The Database

The `databases` library is **schemaless**. It handles everything automatically:

| Feature | How It Works |
|---------|--------------|
| **Auto-create table** | First `save_entity()` creates the table |
| **Auto-add columns** | New fields automatically added |
| **Auto UUID** | `id` generated if not provided |
| **Auto timestamps** | `created_at`, `updated_at` managed |
| **Auto user tracking** | `created_by`, `updated_by` via context (set by kernel) |
| **History tracking** | Every change versioned in `{entity}_history` |
| **Soft delete** | `delete_entity(permanent=False)` |
| **Graceful reads** | `get_entity` returns `None` if table doesn't exist |

### Database API

```python
# Get single entity by ID
entity = await db.get_entity("projects", id)  # dict | None

# Find multiple entities
entities = await db.find_entities(
    "projects",
    where_clause="[workspace_id] = ? AND [status] = ?",
    params=(workspace_id, "active"),
    order_by="[created_at] DESC",
    limit=100,
    offset=0,
    include_deleted=False,
)  # list[dict]

# Save entity (create or update)
entity = await db.save_entity(
    "projects", 
    {"name": "foo", "workspace_id": ws_id},
)  # dict with id, created_at, updated_at, created_by, updated_by

# Delete entity
await db.delete_entity("projects", id, permanent=False)  # Soft delete
await db.delete_entity("projects", id, permanent=True)   # Hard delete

# Count entities
count = await db.count_entities("projects", where_clause="[status] = ?", params=("active",))
```

### Database Access Patterns

**In Routes (use Depends):**
```python
from app_kernel.db import db_connection

@router.get("/projects/{id}")
async def get_project(id: str, db=Depends(db_connection)):
    return await db.get_entity("projects", id)
```

**In Workers (use context manager):**
```python
from app_kernel.db import get_db_connection

async def process_task(ctx, task_id: str):
    async with get_db_connection() as db:
        task = await db.get_entity("tasks", task_id)
        # ... process
```

---

## Models as Code (Optional)

Define dataclasses for **validation**, **defaults**, and **documentation**:

```python
# models.py
from dataclasses import dataclass, asdict
from typing import Optional

@dataclass
class Project:
    """A project groups related services."""
    name: str
    workspace_id: str
    description: Optional[str] = None
    status: str = "active"

@dataclass
class Service:
    """A deployable service within a project."""
    project_id: str
    name: str
    image: str
    port: int = 8000
    replicas: int = 1
```

**Usage:**
```python
from models import Project
from dataclasses import asdict

# Validate then save
project = Project(name=data.name, workspace_id=ws_id)
await db.save_entity("projects", asdict(project))

# Or direct save (AI knows fields from reading models.py)
await db.save_entity("projects", {"name": name, "workspace_id": ws_id})
```

---

## Authentication

### Protecting Routes

```python
from app_kernel import get_current_user, require_admin

@router.get("/projects")
async def list_projects(user=Depends(get_current_user)):
    # user.id, user.email, user.role available
    return await db.find_entities("projects", where_clause="[created_by] = ?", params=(user.id,))

@router.delete("/admin/users/{id}")
async def delete_user(id: str, _=Depends(require_admin)):
    # Only admins can access this
    ...
```

### Auth Configuration

```python
config = ServiceConfig(
    jwt_secret="your-secret",      # Required
    jwt_expiry_hours=24,
    auth_enabled=True,
    allow_self_signup=False,       # Important: disabled by default
)
```

---

## Background Jobs

### Defining Tasks

```python
from app_kernel import JobRegistry, JobContext

registry = JobRegistry()

@registry.task("send_email")
async def send_email(payload: dict, ctx: JobContext):
    to = payload["to"]
    subject = payload["subject"]
    # ... send email
    return {"sent": True}

@registry.task("process_document")
async def process_document(payload: dict, ctx: JobContext):
    doc_id = payload["doc_id"]
    
    # Access database in worker
    async with get_db_connection() as db:
        doc = await db.get_entity("documents", doc_id)
        # ... process
        await db.save_entity("documents", {**doc, "status": "processed"})
```

### Enqueueing Jobs

```python
from app_kernel import get_job_client

@router.post("/documents/{id}/process")
async def start_processing(id: str, user=Depends(get_current_user)):
    client = get_job_client()
    result = await client.enqueue(
        "process_document",
        {"doc_id": id},
        user_id=user.id,
    )
    return {"job_id": result.job_id}
```

### Running Workers

```bash
# Separate process
python -m app_kernel.jobs.worker --tasks my_service.tasks
```

---

## ServiceConfig Options

```python
config = ServiceConfig(
    # Auth
    jwt_secret="your-secret",
    jwt_expiry_hours=24,
    auth_enabled=True,
    allow_self_signup=False,
    
    # Database
    database_name="./data/app.db",
    database_type="sqlite",  # or "postgres", "mysql"
    
    # Redis (enables jobs, rate limiting)
    redis_url="redis://localhost:6379",
    
    # CORS
    cors_origins=["http://localhost:3000"],
    
    # Rate limiting
    rate_limit_requests=100,
    rate_limit_window=60,
)

# Or from environment variables
config = ServiceConfig.from_env()
```

**Environment Variables:**
- `JWT_SECRET` - Required for auth
- `DATABASE_PATH` - SQLite database path
- `DATABASE_TYPE` - sqlite, postgres, mysql
- `REDIS_URL` - Enables jobs and rate limiting
- `CORS_ORIGINS` - Comma-separated origins
- `DEBUG` - Enable debug mode

---

## Multi-Tenant SaaS (Optional)

```python
from app_kernel import require_workspace_member, create_saas_router

# Add SaaS routes (workspaces, members, invites)
app = create_service(
    name="my_saas",
    routers=[router, create_saas_router()],
    config=config,
)

# Protect routes by workspace membership
@router.get("/projects")
async def list_projects(
    workspace_id: str,
    user=Depends(get_current_user),
    _=Depends(require_workspace_member),
    db=Depends(db_connection),
):
    return await db.find_entities("projects", where_clause="[workspace_id] = ?", params=(workspace_id,))
```

---

## Summary

| What | How |
|------|-----|
| **Define schema** | Dataclasses in `models.py` (optional) |
| **Validate input** | Pydantic models |
| **Store data** | `db.save_entity("table", dict)` |
| **Read data** | `db.get_entity("table", id)` |
| **Query data** | `db.find_entities("table", where_clause=...)` |
| **Auth** | `Depends(get_current_user)` |
| **Background jobs** | `await job_client.enqueue("task", payload)` |

**No manifest. No codegen. No ORM. Just Python.**

---

## API Reference

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;">

### Database Connection

| Function | Returns | Description |
|----------|---------|-------------|
| `db_connection` | `Depends` | FastAPI dependency for routes |
| `get_db_connection()` | `AsyncContextManager` | Context manager for workers |

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;">

### Entity Operations

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `get_entity` | `name`, `id`, `include_deleted=False` | `dict \| None` | Get by ID |
| `find_entities` | `name`, `where_clause=`, `params=`, `order_by=`, `limit=`, `offset=` | `list[dict]` | Query |
| `save_entity` | `name`, `entity` | `dict` | Create or update |
| `delete_entity` | `name`, `id`, `permanent=False` | `bool` | Delete |
| `count_entities` | `name`, `where_clause=`, `params=` | `int` | Count |

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;">

### Auto-Generated Fields

| Field | Type | When |
|-------|------|------|
| `id` | `str` (UUID) | Always, if not provided |
| `created_at` | `str` (ISO datetime) | On create |
| `updated_at` | `str` (ISO datetime) | On every save |
| `created_by` | `str` | Via context (set by kernel middleware) |
| `updated_by` | `str` | Via context (set by kernel middleware) |
| `deleted_at` | `str` (ISO datetime) | On soft delete |

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;">

### class `ServiceConfig`

Configuration for `create_service()`.

<details>
<summary><strong>Parameters</strong></summary>

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `jwt_secret` | `str` | None | JWT signing secret (required for auth) |
| `jwt_expiry_hours` | `int` | 24 | Token expiry time |
| `auth_enabled` | `bool` | True | Enable authentication |
| `allow_self_signup` | `bool` | False | Allow `/auth/register` |
| `database_name` | `str` | None | Database name/path |
| `database_type` | `str` | "sqlite" | Database type |
| `redis_url` | `str` | None | Redis URL for jobs/rate limiting |
| `cors_origins` | `list[str]` | ["*"] | CORS allowed origins |
| `rate_limit_requests` | `int` | 100 | Requests per window |
| `rate_limit_window` | `int` | 60 | Window in seconds |
| `debug` | `bool` | False | Enable debug mode |

</details>

<details>
<summary><strong>Methods</strong></summary>

| Method | Returns | Description |
|--------|---------|-------------|
| `from_env()` | `ServiceConfig` | Create from environment variables |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;">

### class `JobRegistry`

Registry for background task handlers.

<details>
<summary><strong>Methods</strong></summary>

| Method | Args | Returns | Description |
|--------|------|---------|-------------|
| `task` | `name`, `timeout=`, `max_attempts=` | decorator | Register a task handler |
| `register` | `name`, `handler`, `timeout=`, `max_attempts=` | None | Register programmatically |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;">

### class `UserIdentity`

User identity from JWT token.

<details>
<summary><strong>Attributes</strong></summary>

| Attribute | Type | Description |
|-----------|------|-------------|
| `id` | `str` | User ID |
| `email` | `str` | User email |
| `role` | `str` | User role ("user", "admin") |
| `is_active` | `bool` | Account active status |
| `workspace_id` | `str \| None` | Current workspace (if SaaS) |

</details>

</div>

---

## API Keys

For service-to-service authentication (CI/CD, scripts, agents).

```python
# Create key (returns plaintext only once)
from app_kernel.api_keys import create_api_key

key = await create_api_key(db, user_id, workspace_id,
    name="CI/CD Pipeline",
    scopes=["deployments:write"],
    expires_in_days=90,
)
# key = {"id": "...", "key": "sk_live_a1b2c3...", ...}

# In requests:
# Authorization: Bearer sk_live_a1b2c3...

# In routes - accept API key OR JWT
from app_kernel.api_keys import create_combined_auth

get_auth = create_combined_auth(get_db_connection, get_current_user)

@router.post("/deployments")
async def deploy(auth=Depends(get_auth)):
    # auth.type = "api_key" or "user"
    # auth.has_scope("deployments:write")
```

---

## Usage Metering

Track API calls per user/workspace for billing and quotas.

**Writes to admin_db via Redis (async, no runtime penalty).**

```python
from app_kernel.metering import track_usage, get_usage, check_quota

# Auto-tracked via middleware (pushed to Redis, worker persists to admin_db)
# Manual tracking for custom metrics:
await track_usage(redis, app="my_app",
    user_id=user.id,
    workspace_id=workspace_id,
    tokens=1500,      # AI tokens
    deployments=1,    # Custom counter
)

# Query usage (from admin_db)
usage = await get_usage(admin_db, app="my_app", workspace_id=ws_id, period="2025-01")
# {"requests": 4521, "tokens": 125000, "deployments": 47}

# Check quota
if not await check_quota(admin_db, app="my_app", workspace_id=ws_id, metric="tokens", limit=100000):
    raise HTTPException(402, "Token limit reached")
```

---

## Audit Logging

Track who changed what, when.

**Auto-captured on save_entity/delete_entity. Writes to admin_db via Redis (async, no runtime penalty).**

```python
from app_kernel.audit import enable_audit, get_audit_logs

# Enable auto-audit (intercepts save_entity/delete_entity)
enable_audit(db, redis_client, app="my_app")

# Now every save_entity/delete_entity is automatically logged
await db.save_entity("deployments", {...})  # → audit event pushed to Redis

# Query logs (from admin_db)
logs = await get_audit_logs(admin_db,
    app="my_app",
    entity="deployments",
    since="2025-01-01",
)
# Returns: action, entity, entity_id, changes (field diffs), user_id, timestamp
```

**Run the admin worker to persist events:**
```bash
python -m app_kernel.admin_worker --redis redis://localhost:6379 --db sqlite:///admin.db
```

---

## Feature Flags

Toggle features without deploy.

```python
from app_kernel.flags import flag_enabled, set_flag

# Check flag
if await flag_enabled(db, "new_dashboard", user_id=user.id):
    return new_dashboard()

# Admin: Set flag
await set_flag(db, "new_dashboard",
    enabled=True,
    rollout_percent=10,           # 10% of users
    workspaces=["ws-123"],        # Specific workspaces
    users=["user-456"],           # Specific users
)
```

---

## Webhooks

Notify external systems on events.

```python
from app_kernel.webhooks import create_webhook, trigger_webhook_event

# Register webhook
webhook = await create_webhook(db, workspace_id,
    url="https://slack.com/webhook/xxx",
    events=["deployment.succeeded", "deployment.failed"],
)

# Trigger event (in your code)
await trigger_webhook_event(db, workspace_id,
    event="deployment.succeeded",
    data={"service": "api", "version": 42},
)
```

---

## OAuth Providers

Google/GitHub login.

```python
# Configure in ServiceConfig
config = ServiceConfig(
    oauth_providers={
        "google": {
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        },
        "github": {...},
    },
)

# Auto-mounted routes:
# GET /auth/oauth/google          → Start OAuth
# GET /auth/oauth/google/callback → Handle callback
# GET /auth/oauth/accounts        → List linked accounts
# DELETE /auth/oauth/google       → Unlink account
```

---

## Caching

Redis-backed with in-memory fallback.

```python
from app_kernel.cache import cache, cached

# Simple get/set
await cache.set("projects:ws-123", projects, ttl=300)
projects = await cache.get("projects:ws-123")
await cache.delete("projects:ws-123")

# Decorator
@cached(ttl=300, key="projects:{workspace_id}")
async def get_projects(workspace_id: str):
    return await db.find_entities("projects", ...)
```

---

## Architecture: App DB vs Admin DB

Kernel uses two databases to separate app data from observability data:

| Database | What | Written By |
|----------|------|------------|
| **App DB** | Your entities, auth, api_keys, flags, webhooks, oauth | Sync (during request) |
| **Admin DB** | Audit logs, usage metrics, traces | Async (via Redis worker) |

**Why separate?**
- Admin writes don't slow down requests
- Admin DB can be shared across apps
- App DB stays clean and fast

**Config:**
```python
config = ServiceConfig(
    name="my_app",                   # Used as "app" field in admin_db
    database_name="app.db",          # App data
    admin_db_url="sqlite:///admin.db",  # Shared observability
    redis_url="redis://localhost:6379",  # Event queue
)
```

**Admin Worker:**
```bash
python -m app_kernel.admin_worker --redis redis://localhost:6379 --db sqlite:///admin.db
```

Consumes from Redis queues and persists to admin_db:
- `admin:audit_events` → `audit_logs` table
- `admin:metering_events` → `usage_summary` table