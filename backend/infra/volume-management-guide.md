# Volume Management Guide

Comprehensive guide to automatic volume generation and management in the Docker deployment system.

## Overview

The `DeploymentSyncer.generate_service_volumes()` method automatically creates standardized volume mappings for services based on service name patterns. This eliminates the need to manually configure volumes for common services and ensures consistent directory structures across environments.

## How It Works

When a service doesn't explicitly define volumes in the configuration, the system automatically generates appropriate volume mappings based on the service name.

### Directory Structure Overview

**Remote Server Structure:**

```
/deployments/{project}/{env}/
├── config/          # Service configurations
├── secrets/         # SSL certificates, API keys
├── data/           # Persistent data storage
├── logs/           # Application logs
└── files/          # Static files, templates
```

**Local Development Structure:**

```
C:\local\{project}\{env}\
├── config\          # Service configurations
├── secrets\         # SSL certificates, API keys
├── data\           # Pulled from servers (per server IP)
│   ├── {sanitized_server_ip}\
│   └── {sanitized_server_ip}\
├── logs\           # Pulled from servers (per server IP)
│   ├── {sanitized_server_ip}\
│   └── {sanitized_server_ip}\
├── backups\        # Pulled from servers (per server IP)
│   ├── {sanitized_server_ip}\
│   └── {sanitized_server_ip}\
└── files\          # Static files, templates
```

**Container Mount Points:**

- Configuration: `/app/config/` (read-only for custom services)
- Secrets: `/app/secrets/` (read-only for custom services)
- Logs: `/app/logs/` (writable for custom services)
- Data: `/app/data/uploads/` (shared uploads for custom services)
- Files: `/app/files/` (read-only templates/assets for custom services)

**Important**: Custom application services should expect to read configuration from `/app/config/`, secrets from `/app/secrets/`, and write logs to `/app/logs/`.

## Service-Specific Volume Mappings

### Database Services

#### PostgreSQL (`postgres`)

```json
[
  "/deployments/{project}/{env}/data/postgres:/var/lib/postgresql/data",
  "/deployments/{project}/{env}/config/postgres:/etc/postgresql"
]
```

**Purpose:**

- **Data persistence**: PostgreSQL database files stored in `/var/lib/postgresql/data`
- **Configuration**: Custom PostgreSQL settings via `/etc/postgresql`

**Usage Example:**

```bash
# Create configuration file
mkdir -p /deployments/myproject/prod/config/postgres
echo "shared_preload_libraries = 'pg_stat_statements'" > /deployments/myproject/prod/config/postgres/postgresql.conf
```

#### Redis (`redis`)

```json
[
  "/deployments/{project}/{env}/data/redis:/data",
  "/deployments/{project}/{env}/config/redis:/usr/local/etc/redis"
]
```

**Purpose:**

- **Data persistence**: Redis RDB/AOF files
- **Configuration**: Redis configuration files

**Usage Example:**

```bash
# Configure Redis persistence
mkdir -p /deployments/myproject/prod/config/redis
cat > /deployments/myproject/prod/config/redis/redis.conf << EOF
save 900 1
save 300 10
save 60 10000
EOF
```

### Search Services

#### OpenSearch (`opensearch`)

```json
[
  "/deployments/{project}/{env}/data/opensearch:/usr/share/opensearch/data",
  "/deployments/{project}/{env}/logs/opensearch:/usr/share/opensearch/logs",
  "/deployments/{project}/{env}/config/opensearch:/usr/share/opensearch/config"
]
```

**Purpose:**

- **Data persistence**: Search indices and cluster state
- **Logging**: OpenSearch service logs
- **Configuration**: OpenSearch settings and security configuration

**Usage Example:**

```bash
# Configure OpenSearch
mkdir -p /deployments/myproject/prod/config/opensearch
cat > /deployments/myproject/prod/config/opensearch/opensearch.yml << EOF
cluster.name: myproject-prod
node.name: node-1
discovery.type: single-node
EOF
```

### Web Server Services

#### Nginx (`nginx`)

```json
[
  "/deployments/{project}/{env}/config/nginx:/etc/nginx",
  "/deployments/{project}/{env}/logs/nginx:/var/log/nginx",
  "/deployments/{project}/{env}/secrets:/etc/ssl/certs:ro"
]
```

**Purpose:**

- **Configuration**: Nginx virtual hosts, upstream definitions
- **Logging**: Access and error logs
- **SSL Certificates**: Read-only access to SSL certificates

**Usage Example:**

```bash
# Configure Nginx virtual host
mkdir -p /deployments/myproject/prod/config/nginx/conf.d
cat > /deployments/myproject/prod/config/nginx/conf.d/app.conf << EOF
server {
    listen 80;
    server_name myapp.com;
    location / {
        proxy_pass http://backend:8000;
    }
}
EOF
```

### Custom Application Services

For any service not matching the above patterns, standardized application volumes are generated:

```json
[
  "/deployments/{project}/{env}/config/{service}:/app/config:ro",
  "/deployments/{project}/{env}/secrets:/app/secrets:ro",
  "/deployments/{project}/{env}/logs/{service}:/app/logs",
  "/deployments/{project}/{env}/data/uploads:/app/data/uploads",
  "/deployments/{project}/{env}/files:/app/files:ro"
]
```

**Purpose:**

- **Configuration**: Service-specific config files (read-only)
- **Secrets**: Shared secrets access (read-only)
- **Logging**: Service-specific log files
- **Uploads**: Shared upload directory for user-generated content
- **Static Files**: Templates, assets, documentation (read-only)

**Usage Example:**

```bash
# Configure custom backend service
mkdir -p /deployments/myproject/prod/config/backend
cat > /deployments/myproject/prod/config/backend/app.json << EOF
{
    "database_url": "postgresql://user:pass@postgres:5432/mydb",
    "redis_url": "redis://redis:6379/0"
}
EOF
```

**Application Code Integration:**
Your custom application code should read configuration from the mounted paths:

```python
# Python example - reading config in container
import json
import os

# Read configuration (mounted at /app/config/)
with open('/app/config/app.json', 'r') as f:
    config = json.load(f)

# Read secrets (mounted at /app/secrets/)
api_key = open('/app/secrets/api-keys/openai.key').read().strip()

# Write logs (mounted at /app/logs/)
import logging
logging.basicConfig(
    filename='/app/logs/application.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Access uploaded files (mounted at /app/data/uploads/)
upload_dir = '/app/data/uploads/'
if os.path.exists(upload_dir):
    files = os.listdir(upload_dir)

# Read templates (mounted at /app/files/)
template_path = '/app/files/email_template.html'
if os.path.exists(template_path):
    with open(template_path, 'r') as f:
        template = f.read()
```

## Directory Structure Best Practices

### Recommended Organization

```
/deployments/myproject/prod/
├── config/
│   ├── backend/
│   │   ├── app.json          # Application configuration
│   │   └── logging.conf      # Logging configuration
│   ├── frontend/
│   │   └── env.json          # Frontend environment variables
│   ├── nginx/
│   │   ├── nginx.conf        # Main Nginx config
│   │   └── conf.d/           # Virtual host configurations
│   ├── postgres/
│   │   └── postgresql.conf   # PostgreSQL settings
│   └── opensearch/
│       └── opensearch.yml    # OpenSearch configuration
├── secrets/
│   ├── ssl/                  # SSL certificates
│   ├── api-keys/            # API keys and tokens
│   └── database-passwords/   # Database credentials
├── data/
│   ├── postgres/            # PostgreSQL data files
│   ├── opensearch/          # Search indices
│   ├── redis/               # Redis persistence
│   └── uploads/             # User-uploaded files
├── logs/
│   ├── backend/             # Backend application logs
│   ├── frontend/            # Frontend application logs
│   ├── nginx/               # Web server logs
│   └── opensearch/          # Search service logs
└── files/
    ├── templates/           # Email/report templates
    ├── assets/             # Static assets
    └── documentation/       # Application docs
```

### Permission Management

Different directories require different security levels:

```bash
# Secure secrets directory (700)
chmod -R 700 /deployments/myproject/prod/secrets/

# Config files readable by services (644)
find /deployments/myproject/prod/config/ -type f -exec chmod 644 {} \;

# Data directories writable by services (755)
chmod -R 755 /deployments/myproject/prod/data/

# Log directories writable by services (755)
chmod -R 755 /deployments/myproject/prod/logs/
```

## Configuration Override

You can override automatic volume generation by explicitly defining volumes in your service configuration:

```json
{
  "project": {
    "services": {
      "postgres": {
        "volumes": [
          "/custom/postgres/data:/var/lib/postgresql/data",
          "/custom/postgres/config:/etc/postgresql"
        ]
      }
    }
  }
}
```

When volumes are explicitly defined, automatic generation is skipped for that service.

## Integration with DeploymentSyncer

The volume paths align with `DeploymentSyncer` sync types, with different behaviors for push vs pull operations:

### Push Operations (Local → Remote)

| Sync Type | Local Path                           | Remote Path                             | Volume Usage                             |
| --------- | ------------------------------------ | --------------------------------------- | ---------------------------------------- |
| `config`  | `C:\local\{project}\{env}\config\*`  | `/deployments/{project}/{env}/config/`  | Service configuration files              |
| `secrets` | `C:\local\{project}\{env}\secrets\*` | `/deployments/{project}/{env}/secrets/` | SSL certs, API keys (secure permissions) |
| `files`   | `C:\local\{project}\{env}\files\*`   | `/deployments/{project}/{env}/files/`   | Templates, static assets                 |

### Pull Operations (Remote → Local with Server Separation)

| Sync Type    | Local Path                                                   | Remote Path                               | Volume Usage                       |
| ------------ | ------------------------------------------------------------ | ----------------------------------------- | ---------------------------------- |
| `data`       | `C:\local\{project}\{env}\data\{sanitized_server_ip}\`       | `/deployments/{project}/{env}/data`       | Database files, uploads per server |
| `logs`       | `C:\local\{project}\{env}\logs\{sanitized_server_ip}\`       | `/deployments/{project}/{env}/logs`       | Application logs per server        |
| `backups`    | `C:\local\{project}\{env}\backups\{sanitized_server_ip}\`    | `/deployments/{project}/{env}/backups`    | Database backups per server        |
| `monitoring` | `C:\local\{project}\{env}\monitoring\{sanitized_server_ip}\` | `/deployments/{project}/{env}/monitoring` | Metrics data per server            |

**Note**: The `{sanitized_server_ip}` replaces dots with underscores (e.g., `192.168.1.100` becomes `192_168_1_100`) to create filesystem-safe directory names.

**Sync Workflow:**

```python
# Push configuration to all servers
DeploymentSyncer.sync_directory("myproject", "prod", "config")

# Deploy services (uses synced config via volumes)
deployer.deploy(project_name="myproject", env="prod")

# Pull logs from specific servers (creates server-specific subdirectories)
DeploymentSyncer.sync_directory("myproject", "prod", "logs", targets=["192.168.1.100", "192.168.1.101"])
```

## Environment Variable Substitution

Volume paths support `{env}` placeholder substitution:

```json
{
  "volumes": ["/deployments/myproject/{env}/config/backend:/app/config:ro"]
}
```

This becomes `/deployments/myproject/dev/config/backend:/app/config:ro` in the dev environment.

## Troubleshooting

### Common Volume Issues

**Permission Denied:**

```bash
# Check directory ownership and permissions
ls -la /deployments/myproject/prod/
sudo chown -R 1000:1000 /deployments/myproject/prod/data/
```

**Container Can't Write to Volume:**

```bash
# Ensure directories exist and are writable
mkdir -p /deployments/myproject/prod/logs/backend
chmod 755 /deployments/myproject/prod/logs/backend
```

**Config Files Not Found:**

```bash
# Verify sync completed successfully
ls -la /deployments/myproject/prod/config/backend/
# Re-sync if necessary
DeploymentSyncer.sync_directory("myproject", "prod", "config")
```

### Volume Mount Validation

The system validates volume syntax before mounting:

- Must contain `:` for host:container mapping
- Must start with `/` for container-only volumes
- Invalid volumes are logged and skipped

---

<div style="background-color:#f8f9fa; border:1px solid #ddd; padding: 16px; border-radius: 8px; margin-bottom: 24px;margin-top: 24px;">

### method `generate_service_volumes`

Generates standardized volume mappings based on service name patterns.

| Parameter | Type  | Description                                     |
| --------- | ----- | ----------------------------------------------- |
| `project` | `str` | Project name for path construction              |
| `env`     | `str` | Environment name (supports `{env}` placeholder) |
| `service` | `str` | Service name for pattern matching               |

**Returns:** `List[str]` - List of volume mount specifications in `host:container[:options]` format

**Service Patterns:**

- `postgres` → Database data and configuration volumes
- `opensearch` → Search data, logs, and configuration volumes
- `redis` → Cache data and configuration volumes
- `nginx` → Web server configuration, logs, and SSL certificates
- **Default** → Standardized application volumes for config, secrets, logs, uploads, and files

**Path Template:** `/deployments/{project}/{env}/[data|config|logs|secrets|files]/[service]/`

</div>
