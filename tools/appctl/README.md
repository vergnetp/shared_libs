# appctl - App Scaffold Generator

Generate production-ready `app_kernel` service scaffolds from simple commands or manifest files.

## Installation

```bash
# Extract tools/ folder into your shared_libs directory
# Result: shared_libs/tools/appctl/
```

## Location

```
project_root/
├── shared_libs/
│   ├── backend/           # Runtime libraries
│   │   ├── app_kernel/
│   │   ├── databases/
│   │   └── ...
│   └── tools/             # Dev tooling
│       └── appctl/
│
└── services/              # Your apps live here
    ├── new_app.bat        # Drag manifest here to generate
    ├── _example.manifest.yaml  # Template to copy
    └── myapp/             # Generated app
```

## Quick Start (Windows)

1. Copy `_example.manifest.yaml` to `myapp.manifest.yaml`
2. Edit the manifest with your app config
3. Drag `myapp.manifest.yaml` onto `new_app.bat`
4. Done! Your app is at `services/myapp/`

## Quick Start (CLI)

```bash
cd services
python ../shared_libs/tools/appctl/appctl.py new myapp --from-manifest myapp.manifest.yaml
```

## Commands

### `appctl new <name>`

Generate a complete app scaffold.

| Flag | Description | Default |
|------|-------------|---------|
| `--from-manifest`, `-m` | Load from manifest YAML file | - |
| `--output`, `-o` | Output directory | `<name>` |
| `--version`, `-v` | App version | `1.0.0` |
| `--description`, `-d` | App description | - |
| `--db` | Database type: `sqlite`, `postgres`, `mysql` | `sqlite` |
| `--db-name` | Database name/path | `./data/<name>.db` |
| `--redis` | Enable Redis | `false` |
| `--no-auth` | Disable authentication | `false` |
| `--allow-signup` | Allow self-signup | `false` |
| `--tasks` | Comma-separated task names | - |
| `--entities` | Entity definitions (see below) | - |

### `appctl manifest <name>`

Generate only a manifest file for later editing.

```bash
python appctl.py manifest myapp --output myapp.manifest.yaml
```

## Entity Definition Format

```bash
# Single entity with fields
--entities "widget:name;color;price"

# Multiple entities
--entities "widget:name;color,order:total;status"

# Field types are inferred as string by default
# Use manifest file for explicit types
```

## Manifest File Format

```yaml
name: myapp
version: 1.0.0
description: "My awesome service"

database:
  type: postgres          # sqlite | postgres | mysql
  name: myapp_db
  host: localhost
  port: 5432
  user: postgres
  password_env: DATABASE_PASSWORD

redis:
  enabled: true
  url_env: REDIS_URL
  key_prefix: "myapp:"

auth:
  enabled: true
  allow_signup: false
  jwt_secret_env: JWT_SECRET
  jwt_expiry_hours: 24

cors:
  origins:
    - http://localhost:3000
    - https://myapp.com
  credentials: true

tasks:
  - process_order
  - send_notification

entities:
  - name: widget
    fields:
      - name: title
        type: string        # string | text | int | float | bool | datetime | json
      - name: price
        type: float
      - name: active
        type: bool
        default: true
    workspace_scoped: true  # Adds workspace_id column
    generate_routes: true   # Generates CRUD routes
    soft_delete: true       # Adds deleted_at column
```

## Generated Structure

```
myapp/
├── __init__.py          # Package init with version
├── main.py              # FastAPI app with create_service()
├── config.py            # Settings from environment
├── db_schema.py         # Database schema init
├── schemas.py           # Pydantic models
├── tasks.py             # Background task handlers (if tasks defined)
├── routes/
│   ├── __init__.py
│   └── widgets.py       # CRUD routes (for each entity)
├── app.manifest.yaml    # Manifest (can regenerate from this)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

## Examples

### 1. Hello World (minimal)

```bash
python appctl.py new hello_api --no-auth
```

### 2. CRUD API (with entities)

```bash
python appctl.py new crud_api \
    --entities "product:name;description;price;stock"
```

### 3. Worker API (with background tasks)

```bash
python appctl.py new worker_api \
    --redis \
    --tasks "send_email,process_payment,generate_report"
```

### 4. Full Production API

```bash
python appctl.py new prod_api \
    --db postgres \
    --redis \
    --entities "customer:name;email,order:total;status;items" \
    --tasks "process_order,send_notification"
```

Or use a manifest file:

```bash
python appctl.py new prod_api --from-manifest prod_api.manifest.yaml
```

## Running Generated Apps

```bash
cd services/myapp

# Copy and edit environment
cp .env.example .env
# Edit .env with your secrets

# Run directly
uvicorn myapp.main:app --reload

# Or with Docker
docker-compose up --build
```

## Architecture

Generated apps follow the `app_kernel` pattern:

- **main.py**: Uses `create_service()` from `app_kernel`
- **config.py**: Frozen settings from environment
- **db_schema.py**: Passed to `schema_init=` parameter
- **routes/**: Imported and passed to `routers=` parameter
- **tasks.py**: Passed to `tasks=` parameter

All infrastructure (auth, logging, health, metrics, jobs) is handled by `app_kernel`.


<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `AppManifest`

Complete app manifest configuration.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| `@classmethod` | `from_yaml` | `path: str` | `AppManifest` | Factory | Load manifest from YAML file. |
| `@classmethod` | `from_dict` | `data: dict` | `AppManifest` | Factory | Create manifest from dictionary. |
| | `to_dict` | | `dict` | Export | Convert manifest to dictionary. |
| | `to_yaml` | `path: str = None` | `str` | Export | Convert to YAML string, optionally write to file. |

</details>

<br>

<details>
<summary><strong>Attributes</strong></summary>

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | required | App name (used for module, redis prefix, etc.) |
| `version` | `str` | `"1.0.0"` | App version |
| `description` | `str` | `""` | App description |
| `database` | `DatabaseConfig` | SQLite | Database configuration |
| `redis` | `RedisConfig` | disabled | Redis configuration |
| `auth` | `AuthConfig` | enabled | Authentication configuration |
| `cors` | `CorsConfig` | `["*"]` | CORS configuration |
| `tasks` | `List[str]` | `[]` | Background task names |
| `entities` | `List[EntityConfig]` | `[]` | Entity definitions |
| `api_prefix` | `str` | `"/api/v1"` | API route prefix |
| `host` | `str` | `"0.0.0.0"` | Server host |
| `port` | `int` | `8000` | Server port |

</details>

</div>


<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### class `ScaffoldGenerator`

Generates app scaffold from manifest.

<details>
<summary><strong>Public Methods</strong></summary>

| Decorators | Method | Args | Returns | Category | Description |
|------------|--------|------|---------|----------|-------------|
| | `__init__` | `manifest: AppManifest`, `output_dir: Path` | | Initialization | Initialize generator with manifest and output path. |
| | `generate` | | | Generation | Generate all scaffold files to output directory. |

</details>

</div>
