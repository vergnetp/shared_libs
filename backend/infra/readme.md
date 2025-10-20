# ProjectDeployer - Docker Multi-Region Deployment System

A Python-based deployment system that automates Docker container orchestration across multiple DigitalOcean regions with zero-downtime deployments, automatic SSL, health monitoring, and backups.

## Table of Contents

- [Quick Start](#quick-start)
- [Prerequisites](#prerequisites)
- [Basic Usage](#basic-usage)
- [Architecture Overview](#architecture-overview)
- [Multi-Zone Deployments](#multi-zone-deployments)
- [Service Configuration](#service-configuration)
- [SSL & Domain Management](#ssl--domain-management)
- [Database Access](#database-access)
- [Backups & Restore](#backups--restore)
- [Health Monitoring](#health-monitoring)
- [Secrets Management](#secrets-management)
- [Advanced Features](#advanced-features)

---

## Quick Start

### 1. Create and Deploy a Project

```python
from backend.infra.project_deployer import ProjectDeployer

# Create new project
project = ProjectDeployer.create("myapp", docker_hub_user="myusername")

# Add services with fluent API
project.add_postgres(version="15") \
       .add_redis() \
       .add_service("api",
                   dockerfile_content={
                       "1": "FROM python:3.11-slim",
                       "2": "WORKDIR /app",
                       "3": "COPY requirements.txt .",
                       "4": "RUN pip install -r requirements.txt",
                       "5": "COPY . .",
                       "6": "EXPOSE 8000",
                       "7": "CMD ['python', 'app.py']"
                   },
                   build_context="C:\\user\\myapp\\api",
                   servers_count=3,
                   domain="api.example.com")

# Deploy to production
project.deploy(env="prod")
```

### 2. Manage Deployments

```python
# Load existing project
project = ProjectDeployer("myapp")

# Check status
project.status()

# View logs
project.logs(service="api", env="prod", lines=100)

# Rollback if needed
project.rollback(env="prod", service="api")
```

---

## Prerequisites

### Required Environment Variables

Create a `.env` file in the project root:

```bash
# Required for remote deployments
DIGITALOCEAN_API_TOKEN=your_do_token_here

# Optional - for automatic SSL via Cloudflare
CLOUDFLARE_API_TOKEN=your_cf_token_here
CLOUDFLARE_EMAIL=your@email.com

# Optional - for email alerts
ADMIN_EMAIL=admin@example.com
GMAIL_APP_PASSWORD=your_gmail_app_password
```

### Local Requirements

- **Docker Desktop** installed and running
- **Python 3.8+** with required packages
- **DigitalOcean Account** (for remote deployments)
- **Cloudflare Account** (optional, for multi-zone & advanced SSL)

### What Gets Automated

The deployer handles:

- ✅ Server provisioning via DigitalOcean API
- ✅ Docker installation on all servers
- ✅ SSH key generation and deployment
- ✅ VPC network setup (per region)
- ✅ Firewall configuration
- ✅ SSL certificates (Let's Encrypt or self-signed)
- ✅ Health monitoring installation
- ✅ Nginx sidecar for service mesh

---

## Basic Usage

### Creating a Project

```python
# Create with defaults
project = ProjectDeployer.create("myapp")

# Create with Docker Hub user
project = ProjectDeployer.create("myapp", docker_hub_user="username")

# Create with custom defaults
project = ProjectDeployer.create(
    "myapp",
    docker_hub_user="username",
    version="1.0.0",
    default_server_ip="localhost"
)
```

### Adding Services

**Database Services:**

```python
# PostgreSQL
project.add_postgres(
    version="15",
    server_zone="lon1",
    servers_count=1,
    startup_order=1
)

# Redis
project.add_redis(
    version="7-alpine",
    servers_count=1
)

# OpenSearch
project.add_opensearch(
    version="2",
    servers_count=1
)
```

**Custom Services:**

```python
# From Dockerfile content
project.add_service(
    "api",
    dockerfile_content={
        "1": "FROM python:3.11-slim",
        "2": "WORKDIR /app",
        # ... more lines
    },
    build_context="/path/to/code",
    servers_count=3,
    domain="api.example.com",
    startup_order=2
)

# From existing Dockerfile
project.add_service(
    "worker",
    dockerfile="config/Dockerfile.worker",
    build_context="/path/to/code",
    servers_count=2,
    startup_order=3
)

# From Docker image
project.add_service(
    "nginx",
    image="nginx:alpine",
    servers_count=1
)
```

**Scheduled Services (Cron Jobs):**

```python
project.add_service(
    "cleanup_job",
    dockerfile_content={"1": "FROM python:3.11-slim", ...},
    build_context="/path/to/code",
    schedule="0 2 * * *",  # 2 AM daily
    startup_order=5
)
```

### Deployment Operations

```python
# Build images (required before first deploy)
project.build(env="prod", push=True)

# Deploy to single zone (auto-detected from config)
project.deploy(env="prod")

# Deploy specific service
project.deploy(env="prod", service="api")

# Deploy without rebuilding
project.deploy(env="prod", build=False)
```

---

## Architecture Overview

### Three-Tier Port System

The system uses a sophisticated port architecture for zero-downtime deployments:

1. **Container Port** (Fixed)

   - Internal to container (e.g., Postgres: 5432, Redis: 6379)
   - Never exposed to host

2. **Host Port** (Toggles)

   - Base: `hash(project_env_service_port) % 1000 + 8000`
   - Secondary: `base + 10000`
   - Range: 8000-8999 (base), 18000-18999 (secondary)
   - Alternates each deployment for zero-downtime

3. **Internal Port** (Stable)
   - Generated: `hash(project_env_service_internal) % 1000 + 5000`
   - Range: **5000-5999**
   - Never changes - apps always connect to this port
   - Nginx routes to actual backend (base or secondary)

### Service Discovery via Nginx Sidecar

Every server runs an nginx sidecar that provides service mesh functionality:

```
Your App Container
  ↓ connects to nginx:5234 (hostname "nginx", internal port - stable)
  ↓ Docker DNS resolves "nginx" to nginx container
  ↓
Nginx Sidecar (same server)
  ↓ routes to backend
  ↓
PostgreSQL Container(s)
  listening on 8357 or 18357 (toggles each deployment)
```

**Benefits:**

- ✅ Apps always connect to `nginx:INTERNAL_PORT` (hostname + port)
- ✅ Zero configuration changes during deployments
- ✅ Automatic failover between old/new versions
- ✅ Works across multiple servers transparently

### Deployment Flow

1. **Startup Order Groups** - Services deploy in order (databases first)
2. **Server Allocation** - Reuses existing servers, creates new as needed
3. **Toggle Deployment** - New container runs alongside old
4. **Health Check** - Verify new container is healthy
5. **Nginx Update** - Update all servers' nginx configs
6. **Traffic Switch** - Nginx redirects to new container
7. **Cleanup** - Remove old container
8. **Server Cleanup** - Destroy unused servers

### Default Server Configuration

All services use these defaults unless overridden:

- `servers_count`: 1
- `server_zone`: "lon1" (London)
- `server_cpu`: 1 vCPU (~$6/month)
- `server_memory`: 1024 MB (1GB)

**Available DigitalOcean Zones:**

- `lon1` - London
- `nyc1`, `nyc3` - New York
- `sfo3` - San Francisco
- `sgp1` - Singapore
- `fra1` - Frankfurt
- `tor1` - Toronto
- `ams3` - Amsterdam
- `blr1` - Bangalore

---

## Multi-Zone Deployments

Deploy your application across multiple geographic regions with automatic load balancing.

### Requirements

1. **Cloudflare Account** with Load Balancer enabled ($5/month)
2. **CLOUDFLARE_API_TOKEN** environment variable
3. **Domain configured** for services

### Configuration

**Option 1: Specify zones per service**

```python
project.add_service(
    "api",
    dockerfile_content={...},
    domain="api.example.com",
    server_zone="lon1",  # This service in London
    servers_count=3
)

project.add_service(
    "api-us",
    dockerfile_content={...},
    domain="api.example.com",  # Same domain
    server_zone="nyc3",  # Different zone
    servers_count=3
)
```

**Option 2: Deploy to multiple zones explicitly**

```python
# Auto-configures from service configs
project.deploy(env="prod")

# Or specify zones explicitly
project.deploy(env="prod", zones=["lon1", "nyc3", "sgp1"])

# Deploy specific service to multiple zones
project.deploy(env="prod", service="api", zones=["lon1", "nyc3"])
```

### How Multi-Zone Works

1. **Parallel Deployment** - Each zone deploys simultaneously
2. **Health Checks** - Verify all zones are healthy
3. **Cloudflare Load Balancer** - Automatically configured with:
   - Health monitoring (HTTPS checks every 60s)
   - Geo-steering (routes users to nearest zone)
   - Automatic failover (removes unhealthy origins)
4. **DNS Management** - Cloudflare handles routing

**User Request Flow:**

```
User in Australia → api.example.com
  ↓
Cloudflare (detects location)
  ↓
Routes to Singapore zone (lowest latency)
  ↓
Nginx Load Balancer in Singapore
  ↓
One of 3 API containers
```

**Fallback Behavior:**
If Cloudflare LB is not available or CLOUDFLARE_API_TOKEN is missing, the system automatically falls back to single-zone deployment using the first zone in the list.

---

## Service Configuration

### Startup Order

Control service dependencies with `startup_order`:

```python
# Databases start first (order 1)
project.add_postgres(startup_order=1)
project.add_redis(startup_order=1)

# Application services after databases (order 2)
project.add_service("api", startup_order=2, ...)

# Background jobs last (order 3)
project.add_service("worker", startup_order=3, ...)

# Nginx load balancer (order 10)
project.add_nginx(startup_order=10)
```

**Services with the same startup_order deploy in parallel.**

### Server Resources

Control server size per service:

```python
project.add_service(
    "api",
    server_cpu=2,      # 2 vCPU cores
    server_memory=4096, # 4GB RAM
    servers_count=5    # 5 servers
)
```

**Cost Calculation:**

- 1 vCPU, 1GB RAM: ~$6/month
- 2 vCPU, 4GB RAM: ~$24/month
- 4 vCPU, 8GB RAM: ~$48/month

### Environment Variables

```python
project.add_service(
    "api",
    env_vars={
        "API_KEY": "secret",
        "DEBUG": "false",
        "DATABASE_URL": "auto"  # Auto-resolved via ResourceResolver
    },
    ...
)
```

### Volumes

```python
project.add_service(
    "api",
    volumes={
        "/local/myapp/prod/config/api": "/app/config:ro",  # Read-only
        "/local/myapp/prod/data/api": "/app/data"          # Read-write
    },
    ...
)
```

### Networks

Services in the same project/environment automatically share a Docker network:

- Network name: `{project}_{env}_network`
- All containers can communicate via container names
- VPC network for cross-server communication (automatic)

---

## SSL & Domain Management

### Three SSL Modes

**1. Self-Signed (Development)**

```python
# Automatic for localhost deployments
project.deploy(env="dev")  # Uses localhost/self-signed
```

- ✅ Zero configuration
- ⚠️ Browser warnings expected
- 🎯 Use for: Local development only

**2. Let's Encrypt Standalone (Production - Basic)**

```bash
# .env file
DIGITALOCEAN_API_TOKEN=your_token
CLOUDFLARE_EMAIL=your@email.com
```

- ✅ Publicly trusted certificates
- ⚠️ 5-10 second downtime during issuance
- ⚠️ No wildcard certificates
- 🎯 Use for: Simple single-zone production

**3. Let's Encrypt DNS-01 (Production - Advanced)**

```bash
# .env file
DIGITALOCEAN_API_TOKEN=your_token
CLOUDFLARE_API_TOKEN=your_cf_token
CLOUDFLARE_EMAIL=your@email.com
```

- ✅ Zero downtime certificate issuance
- ✅ Wildcard certificates supported
- ✅ Automatic DNS management
- ✅ Cloudflare CDN & DDoS protection
- 🎯 Use for: Production multi-zone deployments

### Certificate Management

**Automatic Issuance:**
Certificates are automatically issued when you deploy a service with a `domain` parameter.

**Manual Renewal:**

```python
from backend.infra.nginx_config_generator import NginxConfigGenerator

# Renew all certificates for a project/env
NginxConfigGenerator._renew_certificates(
    target_server="164.92.x.x",
    project="myapp",
    env="prod",
    service="api",
    email="your@email.com",
    cloudflare_api_token="your_token"
)
```

**Certificate Locations:**

- Linux servers: `/local/nginx/certs/letsencrypt/`
- Windows bastion: `C:/local/nginx/certs/letsencrypt/`

---

## Database Access

### From Your Application Code

Applications use `ResourceResolver` to get database connection details:

```python
import psycopg2
from backend.infra.resource_resolver import ResourceResolver

project = "myapp"
env = "prod"

# PostgreSQL
DB_CONFIG = {
    "host": ResourceResolver.get_service_host(project, env, "postgres"),  # Returns "nginx"
    "port": ResourceResolver.get_service_port(project, env, "postgres"),  # Returns internal port (e.g., 5234)
    "database": ResourceResolver.get_db_name(project, env, "postgres"),
    "user": ResourceResolver.get_db_user(project, env, "postgres"),
    "password": ResourceResolver.get_service_password(project, env, "postgres")
}
conn = psycopg2.connect(**DB_CONFIG)

# Or use connection string directly
conn_str = ResourceResolver.get_db_connection_string(project, env, "postgres")
# Returns: 'postgresql://myapp_user:secret123@nginx:5234/myapp_8e9fb088'
conn = psycopg2.connect(conn_str)
```

**Redis:**

```python
import redis

# Manual configuration
r = redis.Redis(
    host=ResourceResolver.get_service_host(project, env, "redis"),  # Returns "nginx"
    port=ResourceResolver.get_service_port(project, env, "redis"),  # Returns internal port
    password=ResourceResolver.get_service_password(project, env, "redis")
)

# Or use connection string
conn_str = ResourceResolver.get_redis_connection_string(project, env, "redis")
# Returns: 'redis://:redispass@nginx:6891/0'
r = redis.from_url(conn_str)
```

### How Connection Resolution Works

**Behind the Scenes:**

1. **App connects to** `nginx:5234` (hostname "nginx", internal port - stable)
2. **Docker DNS resolves** "nginx" to the nginx container on the same server
3. **Nginx sidecar receives** the connection
4. **Nginx routes based on backend location:**
   - **Same server:** Routes directly to container via Docker network (e.g., `new_project_prod_postgres:5432`)
   - **Different server:** Routes to target server IP via VPC network (e.g., `164.92.x.x:8357`)
5. **Backend container** receives the connection

**Security Features:**

- ✅ VPC network encryption between servers (automatic)
- ✅ Passwords stored in mounted files (not environment variables)
- ✅ Secrets copied to all service containers
- ✅ Docker network isolation

### Database Names & Users

**Automatic Generation:**

- Database name: `{project}_{hash8}` (e.g., `myapp_8e9fb088`)
- Database user: `{project}_user` (e.g., `myapp_user`)
- Password: Auto-generated 32-char secure string

**Why hashed names?**

- Prevents collisions across environments
- Ensures uniqueness when multiple projects share infrastructure
- Consistent across deployments (based on project/env/service)

---

## Backups & Restore

### Automatic Backups

Stateful services (Postgres, Redis, OpenSearch) are automatically backed up.

**Default Configuration:**

```python
BACKUP_ENABLED_SERVICES = {
    "postgres": {
        "schedule": "0 2 * * *",  # 2 AM daily
        "retention_days": 7
    },
    "redis": {
        "schedule": "0 3 * * *",  # 3 AM daily
        "retention_days": 7
    },
    "opensearch": {
        "schedule": "0 4 * * *",  # 4 AM daily
        "retention_days": 7
    }
}
```

**Custom Configuration:**

```python
project.add_postgres(
    version="15",
    backup_config={
        "schedule": "0 */6 * * *",  # Every 6 hours
        "retention_days": 14         # Keep 2 weeks
    }
)
```

### How Backups Work

1. **Backup Container Deployed** - Scheduled container on same server as database
2. **Scheduled Execution** - Runs via cron at specified times
3. **Backup Creation:**
   - Postgres: `pg_dump` to `.dump` file
   - Redis: RDB snapshot copy
   - OpenSearch: Snapshot API
4. **Verification** - Integrity check after creation
5. **Storage** - Saved to `/backups` volume on server
6. **Cleanup** - Old backups removed per retention policy

### Managing Backups

**Pull backups to bastion:**

```python
from backend.infra.deployer import Deployer

deployer = Deployer("myapp")

# Pull all backups for an environment
deployer.pull_backups(env="prod")

# Pull specific service backups
deployer.pull_backups(env="prod", service="postgres")
```

**List available backups:**

```python
backups = deployer.list_backups(env="prod", service="postgres")

for backup in backups:
    print(f"{backup['timestamp']}: {backup['size_mb']}MB, {backup['age_hours']}h old")
```

**Restore from backup:**

```python
# Restore latest backup
deployer.rollback(env="prod", service="postgres", timestamp="latest")

# Restore specific backup
deployer.rollback(env="prod", service="postgres", timestamp="20250120_020000")
```

**⚠️ Warning:** Restore operations stop the service, replace data, and restart. This causes downtime.

---

## Health Monitoring

### Automatic Installation via Template Snapshot

Health monitoring **is included in the template snapshot** that all servers are created from.

**How it works:**

1. **First-time setup:** When the first server is needed, the system:

   - Creates a template droplet from base Ubuntu
   - Installs Docker, health monitor, and nginx configs
   - **Schedules health monitor as a cron job** (runs every minute)
   - Takes a snapshot of this fully-provisioned state
   - Destroys the template droplet (saves cost)

2. **Subsequent servers:** All new servers are created from this template snapshot, so they come with:
   - Docker pre-installed
   - Health monitor Docker image pre-built
   - **Cron job pre-configured** to run `docker run health-monitor:latest` every minute

**Implementation Details:**

- **Type:** Cron job (NOT a systemd service or daemon)
- **Schedule:** `* * * * *` (runs every minute)
- **Execution:** Each minute, cron spawns a Docker container that:
  1. Runs `python /app/health_monitor.py`
  2. Calls `HealthMonitor.monitor_and_heal()` once
  3. Container exits
  4. Cron runs it again next minute

**Note:** The `health_monitor.py` script has a `start_monitoring_daemon()` function with an infinite loop, but this is **NOT used** in production. The cron job approach is preferred because:

- Simpler to manage (standard cron)
- Self-healing (if monitor crashes, cron restarts it)
- No need for systemd service management
- Works identically across all servers

### What It Monitors

1. **Server Health:**

   - SSH connectivity
   - Docker daemon status
   - Disk space

2. **Service Health:**

   - Container running status
   - HTTP endpoint checks (for web services)
   - TCP port checks (for databases)

3. **Cross-Server Checks:**
   - All servers monitor each other
   - VPC network connectivity
   - Response times

### Self-Healing

**Leader Election:**

- Oldest healthy server becomes leader
- Leader is responsible for healing actions
- Followers monitor but don't take action

**Automatic Actions:**

1. **Failed Server Detected** (after 3 consecutive failures)
2. **Leader Creates Replacement:**
   - Provisions new server with same specs
   - Installs Docker & health monitor
   - Deploys same services
3. **Health Check** new server
4. **If Healthy:**
   - Updates nginx configs
   - Destroys failed server
   - Updates deployment state
5. **If Unhealthy:**
   - Destroys replacement
   - Retries (max 3 attempts)
   - Sends alert if all attempts fail

### Alert System

**Email Alerts Sent When:**

- Server fails health checks
- Automatic replacement fails
- All servers are down (critical)
- Service deployment fails
- Backup fails

**Configuration:**

```bash
# .env file
ADMIN_EMAIL=admin@example.com
GMAIL_APP_PASSWORD=your_gmail_app_password
```

### Manual Health Check

```python
from backend.infra.health_monitor import HealthMonitor

# Run health check once
HealthMonitor.monitor_and_heal()

# Get server health status
from backend.infra.server_inventory import ServerInventory
servers = ServerInventory.list_all_servers()

for server in servers:
    print(f"{server['ip']}: {server['deployment_status']} - {server['zone']}")
```

---

## Secrets Management

### Automatic Password Generation

Passwords are automatically generated for stateful services (Postgres, Redis, OpenSearch) during first deployment:

- 32 characters
- Alphanumeric (letters + digits)
- Cryptographically secure

**Storage Location:**

- Servers: `/local/{project}/{env}/secrets/{service}/`
- Files: `{service}_password` (e.g., `postgres_password`)

### Manual Rotation

Secrets should be rotated periodically:

```python
from backend.infra.secrets_rotator import SecretsRotator

rotator = SecretsRotator("myapp", "prod")

# Rotate all services
rotator.rotate_all_secrets()

# Rotate specific service
rotator.rotate_postgres_password(service_name="postgres")
rotator.rotate_redis_password(service_name="redis")
rotator.rotate_opensearch_password(service_name="opensearch")
```

**⚠️ Important:** After rotation, you must redeploy services for the new password to take effect:

```python
rotator.rotate_all_secrets()
project.deploy(env="prod")  # Redeploy to apply new passwords
```

### List Secrets

```python
secrets = rotator.list_secrets()

for service, files in secrets.items():
    print(f"{service}:")
    for file in files:
        print(f"  - {file}")
```

### Cleanup Old Backups

```python
# Remove secret backups older than 30 days
rotator.cleanup_old_backups(days_to_keep=30)
```

### Security Best Practices

✅ **Implemented:**

- Passwords stored in files (not environment variables)
- Files mounted read-only where possible
- Automatic backups before rotation
- All secrets copied to all containers (for cross-service communication)

⚠️ **Recommendations:**

- Rotate passwords every 90 days
- Use different passwords per environment
- Back up secrets independently of server backups
- Consider using a secrets management service for production

---

## Advanced Features

### Rollback

Rollback to previous deployment version:

```python
# Rollback to previous version
project.rollback(env="prod", service="api")

# Rollback to specific version
project.rollback(env="prod", service="api", version="1.2.3")
```

**How It Works:**

1. Retrieves previous deployment state
2. Redeploys using old image version
3. Uses zero-downtime toggle deployment
4. Old version becomes "new" deployment

### Deployment Status

```python
# Get status for all zones
status = project.status()
print(status)
# {
#   'lon1': {'active': 3, 'reserve': 1, 'destroying': 0, 'total': 4},
#   'nyc3': {'active': 2, 'reserve': 0, 'destroying': 0, 'total': 2}
# }

# Filter by environment
status = project.status(env="prod")
```

**Server States:**

- `active` - Running one or more services
- `reserve` - Provisioned but not running any services
- `destroying` - Being removed

**Note:** Server inventory is **stateless** - it always queries DigitalOcean directly for current state. Status is stored as droplet tags (e.g., `"status:active"`), not in local files.

### Logs

```python
# Tail logs
project.logs(service="api", env="prod", lines=100)

# Print directly to console
project.print_logs(service="api", env="prod", lines=100)
```

### Update Service Configuration

```python
# Update existing service
project.update_service(
    "api",
    servers_count=5,           # Scale up
    domain="new.example.com",  # Change domain
    env_vars={"DEBUG": "true"} # Update env vars
)

# Deploy changes
project.deploy(env="prod", service="api")
```

### Remove Service

```python
project.delete_service("old-api")
```

**⚠️ Note:** This removes from configuration only. Run deploy to remove running containers.

### Template Snapshot System

**Production servers use pre-baked snapshots for speed:**

- `DOManager.create_server()` - Uses template snapshot (FAST, ~60 seconds)
- `DOManager.create_droplet()` - Full provisioning from scratch (LEGACY, ~5-10 minutes)

**Template Creation Process (first deployment only):**

```python
# Happens automatically on first deployment
snapshot_id = DOManager.get_or_create_template(region="lon1")
# 1. Creates template droplet from base Ubuntu
# 2. Installs Docker (via apt)
# 3. Installs health monitor (via HealthMonitorInstaller)
# 4. Creates nginx config directories
# 5. Takes snapshot (~5-10 minutes)
# 6. Destroys template droplet
# 7. Returns snapshot ID for use
```

**Subsequent server creation:**

```python
# Fast! Just clone from snapshot
server = DOManager.create_server("myserver", "lon1", cpu=2, memory=4096)
# Ready in ~60 seconds with Docker + health monitor already installed
```

**Rebuilding the template:**

```python
# If you need to update the base template
DOManager.delete_template()  # Removes snapshot and any template droplets
# Next deployment will create fresh template
```

### Parallel vs Sequential Deployment

```python
# Parallel (default, faster)
project.deploy(env="prod", zones=["lon1", "nyc3", "sgp1"], parallel=True)

# Sequential (easier debugging)
project.deploy(env="prod", zones=["lon1", "nyc3", "sgp1"], parallel=False)
```

### Build Without Deploy

```python
# Build and push to registry
project.build(env="prod", push=True)

# Build locally only
project.build(env="prod", push=False)

# Then deploy without rebuilding
project.deploy(env="prod", build=False)
```

---

## Troubleshooting

### Common Issues

**1. "No zones configured"**

- Ensure services have `server_zone` parameter
- Or services default to "lon1"
- Check config file syntax

**2. "Cloudflare Load Balancer not enabled"**

- Multi-zone requires Cloudflare LB ($5/month)
- Enable at: https://dash.cloudflare.com
- Or deploy to single zone only

**3. "Health check failed"**

- Service may not be responding on expected port
- Check container logs: `project.logs(service="api", env="prod")`
- Verify Dockerfile exposes correct port

**4. "Cannot connect to database"**

- Ensure shared_libs is in your code path
- Verify ResourceResolver import path
- Check secrets exist: `/local/{project}/{env}/secrets/`

**5. "SSL certificate not trusted"**

- Self-signed certs on localhost are expected
- For production, ensure CLOUDFLARE_EMAIL is set
- Check certificate renewal: `_renew_certificates()`

### Debug Mode

```python
# Enable verbose logging
import logging
logging.basicConfig(level=logging.DEBUG)

# Check deployment state
from backend.infra.deployment_state_manager import DeploymentStateManager
state = DeploymentStateManager.get_current_deployment("myapp", "prod", "api")
print(state)

# Check server inventory
from backend.infra.server_inventory import ServerInventory
servers = ServerInventory.list_all_servers()
for s in servers:
    print(s)
```

---

## API Reference

For complete API documentation including all class methods with parameters, returns, and descriptions, see the separate API documentation file.

**Key Classes:**

- `ProjectDeployer` - Main interface for all operations
- `ResourceResolver` - Service discovery and connection details
- `SecretsRotator` - Password rotation management
- `BackupManager` - Backup configuration and restore
- `HealthMonitor` - Health monitoring and self-healing

---
