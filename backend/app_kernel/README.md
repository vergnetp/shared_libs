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

### Automatic (zero code)

| Feature | Default | What It Does |
|---------|---------|--------------|
| **Health endpoints** | `/healthz`, `/readyz` | Kubernetes-ready health checks |
| **Request ID** | Auto-generated UUID | Every request gets `X-Request-ID` header |
| **Security headers** | Enabled | XSS, CSRF, clickjacking protection |
| **Error handling** | Enabled | Consistent JSON error responses |
| **Structured logging** | JSON to stdout | Request/response logging with context |
| **CORS** | `["*"]` | All origins allowed (configure for prod) |
| **Workspaces** | Always on | Personal workspace auto-created on signup |
| **Registration** | `/auth/register` | Users can sign up |

### Add Env Vars (auto-enabled)

| Feature | Required Env | If Not Set | What It Does |
|---------|--------------|------------|--------------|
| **Database + Auth** | `DATABASE_URL` + `JWT_SECRET` | Auth routes don't mount | Login, register, tokens |
| **Background jobs** | + `REDIS_URL` | `job_client.enqueue()` fails | Async task queue with retries |
| **Rate limiting** | + `REDIS_URL` | No rate limiting | 100 req / 60 sec per user |
| **Caching** | + `REDIS_URL` | Falls back to in-memory | Redis-backed key-value cache |

**SQLite:**
```bash
DATABASE_URL=sqlite:///./data/app.db    # Relative to current working directory
DATABASE_URL=sqlite:////var/data/app.db # Absolute path (4 slashes)
```
- `./data/app.db` → creates `data/` folder where you run `uvicorn`
- Kernel auto-creates parent directories if they don't exist

**Postgres:**
```bash
DATABASE_URL=postgres://user:pass@localhost:5432/myapp
```

**MySQL:**
```bash
DATABASE_URL=mysql://user:pass@localhost:3306/myapp
```

### OAuth (auto-enabled when credentials set)

| Env Vars | If Not Set | What It Does |
|----------|------------|--------------|
| `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` | Google routes not mounted | Google login |
| `GITHUB_CLIENT_ID` + `GITHUB_CLIENT_SECRET` | GitHub routes not mounted | GitHub login |

Routes appear at `/auth/oauth/{provider}` - no code needed.

### Use In Your Code (utilities you wire yourself)

| Feature | What You Do | What It Does |
|---------|-------------|--------------|
| **API Keys** | `create_api_key()` + `create_combined_auth()` | Service-to-service auth |
| **Feature Flags** | `flag_enabled()` / `set_flag()` | Toggle features without deploy |
| **Webhooks** | `create_webhook()` + `trigger_webhook_event()` | Notify external systems |

<details>
<summary>API Keys Example</summary>

```python
from app_kernel.api_keys import create_api_key, create_combined_auth

# Create an API key (admin endpoint)
@router.post("/api-keys")
async def create_key(
    name: str,
    user=Depends(get_current_user),
    db=Depends(db_connection),
):
    key = await create_api_key(db, name=name, user_id=user.id)
    return {"api_key": key["key"]}  # Show once, stored hashed

# Protect endpoint with JWT OR API key
@router.get("/internal/data")
async def get_data(auth=Depends(create_combined_auth())):
    # auth.user_id available from either JWT or API key
    return {"user": auth.user_id}
```

</details>

<details>
<summary>Feature Flags Example</summary>

```python
from app_kernel.flags import flag_enabled, set_flag

# Set a flag (admin)
await set_flag(db, "new_dashboard", enabled=True, rollout_percent=50)

# Check in your code
@router.get("/dashboard")
async def dashboard(db=Depends(db_connection)):
    if await flag_enabled(db, "new_dashboard"):
        return {"version": "v2"}
    return {"version": "v1"}
```

</details>

<details>
<summary>Webhooks Example</summary>

```python
from app_kernel.webhooks import create_webhook, trigger_webhook_event

# User registers a webhook (receives ALL events)
@router.post("/webhooks")
async def register_webhook(
    url: str,
    user=Depends(get_current_user),
    db=Depends(db_connection),
):
    webhook = await create_webhook(db, url=url, workspace_id=user.workspace_id)
    return webhook

# Trigger when something happens - sent to ALL webhooks
async def deploy_service(...):
    deployment = await db.save_entity("deployments", {...})
    
    # All webhooks receive this event
    await trigger_webhook_event(db, workspace_id, "deployment.created", {
        "deployment_id": deployment["id"],
        "status": "running",
    })

# Receiver handles events they care about:
# POST payload: {"event": "deployment.created", "data": {...}, "timestamp": "..."}
```

</details>

### Requires Extra Infrastructure

| Feature | Requirements | If Not Set | What It Does |
|---------|--------------|------------|--------------|
| **Audit logging** | `REDIS_URL` | No audit trail | Auto-logs entity changes |
| **Usage metering** | `REDIS_URL` | No usage data | Auto-tracks API calls |

Both are **auto-enabled** when `REDIS_URL` is set. No code changes needed.

**Embedded worker (default):** Runs as background task in uvicorn workers - zero setup.

**Separate worker (production):** Set `ADMIN_WORKER_EMBEDDED=false` and run separately.

<details>
<summary>Separate Worker Setup (optional)</summary>

For production, you may want the admin worker as a separate process:

```bash
# Disable embedded worker in your API
ADMIN_WORKER_EMBEDDED=false

# Run worker separately
REDIS_URL=redis://localhost:6379
ADMIN_DB_URL=sqlite:///admin.db
python -m app_kernel.admin_worker
```

**Docker Compose:**
```yaml
services:
  api:
    environment:
      - DATABASE_URL=sqlite:///./data/app.db
      - REDIS_URL=redis://redis:6379
      - ADMIN_WORKER_EMBEDDED=false
    
  admin-worker:
    command: python -m app_kernel.admin_worker
    environment:
      - REDIS_URL=redis://redis:6379
      - ADMIN_DB_URL=sqlite:///./data/admin.db
```

**Why separate?** Better isolation, separate scaling, different DB for admin data.

</details>

**What gets logged:**
- **Audit:** Every `save_entity()` / `delete_entity()` → old/new values, user_id
- **Metering:** Every API request → endpoint, method, status, latency, bytes

**Data retention:** Audit can be purged. Metering should NOT (billing data).

### Dev Auto-Start (Docker)

Kernel auto-starts Redis/Postgres via Docker when:
- URL points to `localhost` 
- Service isn't already running
- Docker is available

```bash
# Just set your URLs
DATABASE_URL=postgres://postgres:postgres@localhost:5432/myapp
REDIS_URL=redis://localhost:6379

# On startup:
# ✓ PostgreSQL started at localhost:5432
# ✓ Redis started at localhost:6379
```

Containers (`appkernel-redis`, `appkernel-postgres`) persist between runs. Remote URLs are ignored (production-safe).

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
    return await db.save_entity("widgets", {
        "name": data.name,
        "color": data.color,
        "owner_id": user.id,  # Track ownership explicitly
    })


@router.get("")
async def list_widgets(user=Depends(get_current_user), db=Depends(db_connection)):
    return await db.find_entities("widgets", where_clause="[owner_id] = ?", params=(user.id,))


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

**Run (minimal):**
```bash
DATABASE_URL=sqlite:///./data/app.db JWT_SECRET=dev-secret uvicorn main:app --reload
```

**Run (with jobs & rate limiting):**
```bash
DATABASE_URL=sqlite:///./data/app.db JWT_SECRET=dev-secret REDIS_URL=redis://localhost:6379 uvicorn main:app --reload
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
| **History tracking** | Every change versioned in `{entity}_history` |
| **Soft delete** | `delete_entity(permanent=False)` |
| **Graceful reads** | `get_entity` returns `None` if table doesn't exist |
| **Who did it** | Via audit_logs in admin_db (see Audit Logging) |

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
)  # dict with id, created_at, updated_at

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
    return await db.find_entities("projects", where_clause="[owner_id] = ?", params=(user.id,))

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

## Configuration

### Minimal (dev)

```bash
# Creates ./data/app.db relative to where you run uvicorn
DATABASE_URL=sqlite:///./data/app.db JWT_SECRET=dev-secret uvicorn main:app --reload
```

### Production (SQLite)

```bash
DATABASE_URL=sqlite:///./data/app.db
JWT_SECRET=your-production-secret
REDIS_URL=redis://localhost:6379
CORS_ORIGINS=https://myapp.com
```

### Production (Postgres)

```bash
DATABASE_URL=postgres://myapp:secret@db.example.com:5432/myapp
JWT_SECRET=your-production-secret
REDIS_URL=redis://localhost:6379
CORS_ORIGINS=https://myapp.com
```

### Production (MySQL)

```bash
DATABASE_URL=mysql://myapp:secret@db.example.com:3306/myapp
JWT_SECRET=your-production-secret
REDIS_URL=redis://localhost:6379
CORS_ORIGINS=https://myapp.com
```

### All Options

```python
config = ServiceConfig(
    # Auth
    jwt_secret="your-secret",       # Required in prod, default: "dev-secret-change-me"
    jwt_expiry_hours=24,            # Default: 24
    auth_enabled=True,              # Default: True
    
    # Workspaces (always on)
    saas_enabled=True,              # Default: True
    
    # OAuth (routes auto-mount when configured)
    oauth_providers={               # Default: {} (none)
        "google": {"client_id": "...", "client_secret": "..."},
        "github": {"client_id": "...", "client_secret": "..."},
    },
    
    # Database URL
    database_url="sqlite:///./data/app.db",   # Or postgres://... or mysql://...
    
    # Redis (enables jobs, rate limiting, caching, audit/metering)
    redis_url="redis://localhost:6379",  # Default: None (features disabled)
    
    # CORS
    cors_origins=["https://myapp.com"],  # Default: ["*"]
    cors_credentials=True,               # Default: True
    
    # Rate limiting (requires Redis)
    rate_limit_enabled=True,        # Default: True (but no-op without Redis)
    rate_limit_requests=100,        # Default: 100 per window
    rate_limit_window=60,           # Default: 60 seconds
    
    # Debug
    debug=False,                    # Default: False
)

# Or load from environment
config = ServiceConfig.from_env()
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | — | `sqlite:///./path`, `postgres://...`, `mysql://...` |
| `JWT_SECRET` | `"dev-secret-change-me"` | JWT signing secret (change in prod!) |
| `REDIS_URL` | — | Redis URL (enables jobs, rate limiting, cache, audit, metering) |
| `ADMIN_WORKER_EMBEDDED` | `"true"` | Run admin worker in uvicorn workers (set `false` to run separately) |
| `ADMIN_DB_URL` | `DATABASE_URL` | Admin worker database (if different from app) |
| `CORS_ORIGINS` | `"*"` | Comma-separated origins |
| `GOOGLE_CLIENT_ID` | — | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | — | Google OAuth secret |
| `GITHUB_CLIENT_ID` | — | GitHub OAuth client ID |
| `GITHUB_CLIENT_SECRET` | — | GitHub OAuth secret |
| `DEBUG` | `"false"` | Debug mode |

---

## Workspaces (Multi-Tenant)

Workspaces are **always enabled**. Every user gets a personal workspace on signup.

| Scenario | How It Works |
|----------|--------------|
| **Single user** | Personal workspace auto-created, transparent |
| **Team** | Create additional workspaces, invite members |
| **Enterprise** | Same model scales - workspaces are companies |

```python
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

**Auto-mounted routes:**
- `POST /workspaces` - Create workspace
- `GET /workspaces` - List user's workspaces
- `POST /workspaces/{id}/invite` - Invite member
- `POST /workspaces/join/{token}` - Accept invite
- `DELETE /workspaces/{id}/members/{user_id}` - Remove member

---

## Summary

**Automatic (zero code):**
- Health endpoints, security headers, structured logging
- Workspaces (personal workspace created on signup)
- Registration (`/auth/register`)
- OAuth (when credentials set)

**Needs env vars:**
| Feature | Required |
|---------|----------|
| **Auth** | `DATABASE_URL` + `JWT_SECRET` |
| **Jobs/rate limiting/cache** | + `REDIS_URL` |
| **Audit + metering** | + `REDIS_URL` (auto-enabled) + run admin worker |
| **OAuth** | + `GOOGLE_CLIENT_ID` or `GITHUB_CLIENT_ID` |

**You wire yourself:**
| Feature | How |
|---------|-----|
| **API Keys** | `create_api_key()` + `create_combined_auth()` |
| **Feature flags** | `await flag_enabled(db, "flag_name")` |
| **Webhooks** | `trigger_webhook_event()` in your code |

**Common patterns:**
| Pattern | Code |
|---------|------|
| **Protect route** | `user=Depends(get_current_user)` |
| **Admin only** | `_=Depends(require_admin)` |
| **Workspace member** | `_=Depends(require_workspace_member)` |
| **Get entity** | `await db.get_entity("table", id)` |
| **Save entity** | `await db.save_entity("table", {...})` |
| **Query** | `await db.find_entities("table", where_clause=...)` |
| **Background job** | `await job_client.enqueue("task", payload)` |

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
| `deleted_at` | `str` (ISO datetime) | On soft delete |

**Note:** For "who did it", use audit_logs in admin_db (see Audit Logging section).

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
REDIS_URL=redis://localhost:6379 ADMIN_DB_URL=sqlite:///admin.db python -m app_kernel.admin_worker
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

Notify external systems on events. All events sent to all webhooks - receiver decides what to handle.

```python
from app_kernel.webhooks import create_webhook, trigger_webhook_event

# Register webhook (receives ALL events)
webhook = await create_webhook(db, workspace_id,
    url="https://slack.com/webhook/xxx",
)

# Trigger event (in your code) - sent to ALL webhooks
await trigger_webhook_event(db, workspace_id,
    event="deployment.succeeded",
    data={"service": "api", "version": 42},
)
# Payload: {"event": "deployment.succeeded", "data": {...}, "timestamp": "..."}
```

---

## OAuth (Google/GitHub)

**Auto-enabled** when credentials are set:

```bash
# Add to environment - routes auto-mount
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...

GITHUB_CLIENT_ID=...
GITHUB_CLIENT_SECRET=...
```

**Auto-mounted routes:**
- `GET /auth/oauth/google` - Start Google OAuth
- `GET /auth/oauth/google/callback` - Handle callback
- `GET /auth/oauth/github` - Start GitHub OAuth  
- `GET /auth/oauth/github/callback` - Handle callback
- `GET /auth/oauth/accounts` - List linked accounts
- `DELETE /auth/oauth/{provider}` - Unlink account

**Frontend usage:**
```javascript
// Redirect user to start OAuth
window.location = '/api/v1/auth/oauth/google?redirect_uri=/dashboard';
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

Kernel separates app data from observability data:

| Database | Config | What | Written |
|----------|--------|------|---------|
| **App DB** | `DATABASE_URL` | Your entities, users, api_keys, flags, webhooks | Sync (during request) |
| **Admin DB** | `ADMIN_DB_URL` (or same as App DB) | Audit logs, usage metrics | Async (via Redis) |

**Simple setup (everything in one DB):**
```bash
DATABASE_URL=sqlite:///./data/app.db
REDIS_URL=redis://localhost:6379   # Enables audit + metering
# Embedded worker handles it - no separate process needed
```

**Production setup (separate admin DB):**
```bash
DATABASE_URL=postgres://...        # App data
REDIS_URL=redis://...
ADMIN_DB_URL=postgres://...        # Audit/metering data
ADMIN_WORKER_EMBEDDED=false        # Run worker separately for isolation
```

**Why separate admin DB?**
- Audit/metrics don't bloat your app database
- Can use different retention policies
- Admin DB can be shared across multiple apps
