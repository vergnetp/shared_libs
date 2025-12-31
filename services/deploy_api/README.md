# Deploy API

Deployment Management Service - API wrapper for infrastructure deployment system.

## Overview

Deploy API provides REST endpoints for:
- **Workspace management** - Multi-tenant organization support
- **Project configuration** - Docker-based service definitions
- **Service management** - Add/remove services to projects
- **Deployment triggering** - Queue deployments with status tracking
- **Credentials management** - Encrypted storage for cloud provider tokens

## Architecture

```
deploy_api/
├── manifest.yaml        # Entity definitions (source of truth)
├── config.py           # Service configuration
├── main.py             # Application entry point
├── worker.py           # Background job processor
├── _gen/               # AUTO-GENERATED from manifest
│   ├── db_schema.py    # Database tables
│   ├── schemas.py      # Pydantic models
│   ├── crud.py         # CRUD operations
│   └── routes/         # (Optional) Basic CRUD routes
├── src/                # CUSTOM business logic
│   ├── schemas.py      # API-specific schemas
│   ├── stores.py       # Higher-level store operations
│   ├── deps.py         # FastAPI dependencies
│   ├── access.py       # Workspace access checker
│   ├── routes/         # Custom API routes
│   │   ├── workspaces.py
│   │   ├── projects.py
│   │   └── deployments.py
│   └── workers/        # Background job handlers
│       └── tasks.py
├── static/             # Web UI assets
└── data/               # SQLite database (gitignored)
```

## Quick Start

### 1. Environment Setup

```bash
# Required
export JWT_SECRET="your-secret-key"

# Optional (for job queue)
export REDIS_URL="redis://localhost:6379"

# Optional (database defaults to ./data/deploy.db)
export DEPLOY_DATABASE_PATH="./data/deploy.db"
```

### 2. Run the API

```bash
# With app_kernel
python main.py

# Development mode
DEPLOY_DEBUG=true python main.py
```

### 3. Run Workers (optional, requires Redis)

```bash
python worker.py
```

## API Endpoints

### Workspaces

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/workspaces` | Create workspace |
| GET | `/api/v1/workspaces` | List user's workspaces |
| GET | `/api/v1/workspaces/{id}` | Get workspace details |
| DELETE | `/api/v1/workspaces/{id}` | Delete workspace (owner only) |
| POST | `/api/v1/workspaces/{id}/members` | Add member |
| DELETE | `/api/v1/workspaces/{id}/members/{user_id}` | Remove member |

### Projects

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/workspaces/{ws}/projects` | Create project |
| GET | `/api/v1/workspaces/{ws}/projects` | List projects |
| GET | `/api/v1/workspaces/{ws}/projects/{name}` | Get project |
| PATCH | `/api/v1/workspaces/{ws}/projects/{name}` | Update project |
| DELETE | `/api/v1/workspaces/{ws}/projects/{name}` | Delete project |

### Services

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `.../projects/{name}/services` | Add service |
| GET | `.../projects/{name}/services` | List services |
| DELETE | `.../projects/{name}/services/{svc}` | Remove service |

### Credentials

| Method | Endpoint | Description |
|--------|----------|-------------|
| PUT | `.../projects/{name}/credentials/{env}` | Set credentials |
| GET | `.../projects/{name}/credentials/{env}` | Check credentials status |
| DELETE | `.../projects/{name}/credentials/{env}` | Delete credentials |

### Deployments

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `.../projects/{name}/deploy` | Trigger deployment |
| GET | `.../projects/{name}/deploy/{job_id}` | Get deployment status |
| GET | `.../projects/{name}/deployments` | List deployment history |
| GET | `.../projects/{name}/status/{env}` | Get live container status |
| POST | `.../projects/{name}/rollback/{env}` | Trigger rollback |

## Development

### Regenerate from Manifest

After modifying `manifest.yaml`:

```bash
cd services/deploy_api
appctl generate
```

This regenerates `_gen/` without touching `src/`.

### Adding New Entities

1. Add entity to `manifest.yaml`
2. Run `appctl generate`
3. Add custom store methods in `src/stores.py`
4. Add API routes in `src/routes/`

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DEPLOY_JWT_SECRET` | `change-me-in-production` | JWT signing secret |
| `DEPLOY_JWT_EXPIRY_HOURS` | `24` | Token expiry |
| `DEPLOY_DATABASE_PATH` | `./data/deploy.db` | SQLite path |
| `DEPLOY_DATABASE_TYPE` | `sqlite` | Database type |
| `DEPLOY_REDIS_URL` | (none) | Redis URL for jobs |
| `DEPLOY_DEBUG` | `false` | Enable debug mode |
| `DEPLOY_HOST` | `0.0.0.0` | Server host |
| `DEPLOY_PORT` | `8000` | Server port |

---

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `WorkspaceStore`

Higher-level workspace operations with membership management.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `create` | `name: str`, `owner_id: str`, `plan: str = "free"` | `Dict[str, Any]` | CRUD | Create workspace and add owner as member |
| | `get` | `workspace_id: str` | `Optional[Dict]` | CRUD | Get workspace by ID |
| | `get_by_name` | `name: str` | `Optional[Dict]` | CRUD | Get workspace by name |
| | `list_for_user` | `user_id: str` | `List[Dict]` | Query | List workspaces user is member of |
| | `delete` | `workspace_id: str` | `bool` | CRUD | Delete workspace |
| | `add_member` | `workspace_id: str`, `user_id: str`, `role: str = "member"` | `Dict` | Membership | Add member to workspace |
| | `remove_member` | `workspace_id: str`, `user_id: str` | `bool` | Membership | Remove member from workspace |
| | `is_member` | `user_id: str`, `workspace_id: str` | `bool` | Access | Check if user is member |
| | `is_owner` | `user_id: str`, `workspace_id: str` | `bool` | Access | Check if user is owner |
| | `get_role` | `user_id: str`, `workspace_id: str` | `Optional[str]` | Access | Get user's role in workspace |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `ProjectStore`

Project operations with config management.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `create` | `workspace_id: str`, `name: str`, `docker_hub_user: str`, `version: str = "latest"`, `created_by: str = None` | `Dict` | CRUD | Create project with initial config |
| | `get` | `workspace_id: str`, `name: str` | `Optional[Dict]` | CRUD | Get project by workspace and name |
| | `list` | `workspace_id: str` | `List[Dict]` | Query | List all projects in workspace |
| | `update` | `workspace_id: str`, `name: str`, `**updates` | `Optional[Dict]` | CRUD | Update project fields |
| | `delete` | `workspace_id: str`, `name: str` | `bool` | CRUD | Delete project |
| | `get_config` | `workspace_id: str`, `name: str` | `Optional[Dict]` | Config | Get parsed project config |
| | `save_config` | `workspace_id: str`, `name: str`, `config: Dict` | `bool` | Config | Save project config |
| | `add_service` | `workspace_id: str`, `name: str`, `service_name: str`, `service_config: Dict` | `bool` | Services | Add service to project |
| | `remove_service` | `workspace_id: str`, `name: str`, `service_name: str` | `bool` | Services | Remove service from project |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `CredentialsStore`

Credentials storage with encryption support.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `set` | `workspace_id: str`, `project_name: str`, `env: str`, `credentials: Dict` | `Dict` | CRUD | Store credentials (encrypted) |
| | `get` | `workspace_id: str`, `project_name: str`, `env: str` | `Optional[Dict]` | CRUD | Retrieve and decrypt credentials |
| | `delete` | `workspace_id: str`, `project_name: str`, `env: str` | `bool` | CRUD | Delete credentials |
| | `exists` | `workspace_id: str`, `project_name: str`, `env: str` | `bool` | Query | Check if credentials exist |

</details>

</div>

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `DeploymentStore`

Deployment runs and state management.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `create_run` | `job_id: str`, `workspace_id: str`, `project_name: str`, `env: str`, `triggered_by: str`, `services: List[str] = None` | `Dict` | Runs | Record new deployment run |
| | `update_run` | `job_id: str`, `**updates` | `bool` | Runs | Update deployment run by job_id |
| | `get_run` | `job_id: str` | `Optional[Dict]` | Runs | Get deployment run with parsed JSON |
| | `list_runs` | `workspace_id: str`, `project_name: str = None`, `env: str = None`, `limit: int = 50` | `List[Dict]` | Runs | List deployment runs |
| | `get_state` | `workspace_id: str`, `project_name: str`, `env: str` | `Dict` | State | Get deployment state |
| | `save_state` | `workspace_id: str`, `project_name: str`, `env: str`, `state: Dict`, `deployed_by: str = None` | `bool` | State | Save deployment state |

</details>

</div>
