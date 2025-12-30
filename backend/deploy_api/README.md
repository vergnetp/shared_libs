# Deploy API

REST API for deployment management with web UI. Wraps the `infra` library to provide HTTP endpoints for managing projects, services, and deployments.

## Features

- **Web Dashboard** - Full UI at `/` for managing deployments
- **Workspace Management** - Multi-tenant isolation via workspaces
- **Project CRUD** - Create, configure, and manage deployment projects
- **Service Configuration** - Add postgres, redis, custom APIs, etc.
- **Background Deployments** - Async deployment via job queue
- **Credentials Management** - Encrypted storage for DO tokens, etc.
- **Live Status** - Query running containers
- **Rollback** - Quick rollback to previous deployment

## Quick Start

```bash
# Set environment
export JWT_SECRET="your-secret-key-change-in-production"
export REDIS_URL="redis://localhost:6379"  # Optional, for background jobs

# Run
cd backend
uvicorn deploy_api.main:app --reload --port 8080

# Open browser
# http://localhost:8080
```

## Web UI

The dashboard at `/` provides:

1. **Login/Signup** - Create account and authenticate
2. **Workspaces** - Create and switch between tenants
3. **Projects** - Create projects, add services, configure credentials
4. **Deploy** - Trigger deployments with one click
5. **Status** - Monitor deployment progress in real-time

![Dashboard](docs/dashboard.png)

## API Overview

### Workspaces (Tenants)

```bash
# Create workspace
POST /api/v1/workspaces
{"name": "my-org"}

# List your workspaces
GET /api/v1/workspaces
```

### Projects

```bash
# Create project
POST /api/v1/workspaces/{workspace_id}/projects
{"name": "my-app", "docker_hub_user": "myuser"}

# Add service
POST /api/v1/workspaces/{workspace_id}/projects/my-app/services
{"name": "api", "git_repo": "github.com/me/api@main", "ports": ["8000"]}

# Add standard service (auto-configured)
POST /api/v1/workspaces/{workspace_id}/projects/my-app/services
{"name": "postgres"}
```

### Credentials

```bash
# Set credentials for environment
PUT /api/v1/workspaces/{workspace_id}/projects/my-app/credentials/prod
{
  "digitalocean_token": "dop_v1_xxx",
  "docker_hub_user": "myuser",
  "docker_hub_password": "xxx"
}
```

### Deployments

```bash
# Trigger deployment
POST /api/v1/workspaces/{workspace_id}/projects/my-app/deploy
{"env": "prod"}
# Returns: {"job_id": "xxx", "status": "queued"}

# Check status
GET /api/v1/workspaces/{workspace_id}/projects/my-app/deploy/{job_id}
# Returns: {"status": "running", "step": "building api", "progress": 45}

# List history
GET /api/v1/workspaces/{workspace_id}/projects/my-app/deployments

# Live status
GET /api/v1/workspaces/{workspace_id}/projects/my-app/status/prod

# Rollback
POST /api/v1/workspaces/{workspace_id}/projects/my-app/rollback/prod
```

## Architecture

```
deploy_api/
├── main.py              # FastAPI app (uses app_kernel bootstrap)
├── config.py            # Settings
├── schemas.py           # Pydantic models
├── db_schema.py         # SQL schema
├── stores.py            # DB-backed storage
├── access.py            # Workspace access checker
├── deps.py              # Dependency injection
├── static/
│   └── index.html       # Web dashboard UI
├── routes/
│   ├── workspaces.py    # Workspace CRUD
│   ├── projects.py      # Project & service management
│   └── deployments.py   # Deploy, status, rollback
└── workers/
    └── deploy.py        # Background deployment jobs
```

## Database Schema

- `workspaces` - Tenant/organization
- `workspace_members` - User membership
- `projects` - Project configuration (replaces JSON files)
- `credentials` - Encrypted credentials
- `deployment_runs` - Deployment history
- `deployment_state` - Last deployment state per env

## Integration with Infra

The `workers/deploy.py` calls your existing infra code:

```python
from backend.infra.deployer import Deployer

deployer = Deployer(user=workspace_id, project_name=project_name)
result = deployer.deploy(env=env, credentials=credentials)
```

The `workspace_id` maps to infra's `user` parameter, providing tenant isolation.

## Configuration

| Env Var | Description | Default |
|---------|-------------|---------|
| `JWT_SECRET` | JWT signing secret | (required) |
| `REDIS_URL` | Redis for job queue | None (jobs disabled) |
| `DATABASE_NAME` | Database name/path | ./data/deploy.db |
| `DATABASE_TYPE` | sqlite, postgres, mysql | sqlite |
| `DATABASE_HOST` | DB host (postgres/mysql) | localhost |
| `DATABASE_PORT` | DB port | (default for type) |
| `DATABASE_USER` | DB user | None |
| `DATABASE_PASSWORD` | DB password | None |
| `AUTH_ENABLED` | Enable authentication | true |
| `ALLOW_SELF_SIGNUP` | Allow user registration | true |

## Dependencies

Uses `shared_libs/backend/databases` module for database operations (supports SQLite, PostgreSQL, MySQL).

## Self-Hosting

1. Deploy this API on a server
2. Create a workspace for yourself
3. Use it to deploy your other apps

```bash
# First deployment (manual)
cd deploy_api
uvicorn deploy_api.main:app --host 0.0.0.0 --port 8080

# Then use the API to deploy everything else!
```
