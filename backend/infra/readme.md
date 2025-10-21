# ProjectDeployer - Docker Multi-Region Deployment System

A Python-based deployment system that automates Docker container orchestration across multiple DigitalOcean regions with zero-downtime deployments, automatic SSL, health monitoring, and backups.

## Table of Contents

- [Quick Start](#quick-start)
- [Simplified Service Creation](#simplified-service-creation)
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
- [Auto-Scaling](#auto-scaling)
- [Advanced Features](#advanced-features)

---

## Quick Start

### 1. Create and Deploy a Project

```python
from backend.infra.project_deployer import ProjectDeployer

# Create new project
project = ProjectDeployer.create("myapp", docker_hub_user="myusername")

# Add services with fluent API
project.add_postgres() \
       .add_redis() \
       .add_python_service(
           "api",
           depends_on=["postgres", "redis"],
           git_repo="https://github.com/user/api.git@main",
           git_token="tk8787",
           command="uvicorn main:app --host 0.0.0.0",
           port=8000,
           servers_count=3,
           domain="api.example.com",
           auto_scaling=True
       ) \
       .add_react_service(
           "web",
           depends_on=["api"],
           git_repo="https://github.com/user/web.git@main",
           git_token="tk8787",
           domain="www.example.com",
           servers_count=3,
           auto_scaling=True
       )

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

## Simplified Service Creation

The deployer includes convenience methods that automatically generate optimized Dockerfiles:

**Python Services** - `add_python_service()`

- Automatically handles requirements.txt installation
- Supports multiple requirements files
- Best practices (pip --no-cache-dir, slim images)

**Node.js Services** - `add_nodejs_service()`

- Supports npm, yarn, or pnpm
- Optional build step for TypeScript/Next.js
- Production-optimized dependencies

**React/SPA Services** - `add_react_service()`

- Multi-stage build (Node.js build + Nginx serve)
- Works with React, Vue, Angular, Svelte
- Automatic gzip, caching, SPA routing

**All Methods Support:**

- ✅ Git repository integration (clone from GitHub/GitLab)
- ✅ Automatic dependency management (`depends_on`)
- ✅ Auto-scaling configuration
- ✅ Service dependency tracking
- ✅ Custom Dockerfile override

**Example - Complete Stack in 20 Lines:**

```python
from backend.infra.project_deployer import ProjectDeployer

project = ProjectDeployer.create("myapp")

project.add_postgres() \
       .add_redis() \
       .add_python_service(
           "api",
           depends_on=["postgres", "redis"],
           git_repo="https://github.com/user/api.git@main",
           git_token="tk8787",
           command="uvicorn main:app --host 0.0.0.0",
           port=8000,
           servers_count=3,
           domain="api.example.com",
           auto_scaling=True
       ) \
       .add_react_service(
           "web",
           depends_on=["api"],
           git_repo="https://github.com/user/web.git@main",
           git_token="tk8787",
           domain="www.example.com",
           servers_count=3,
           auto_scaling=True
       ) \
       .deploy(env="prod")
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
- **Git** (optional, for git_repo feature)

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
- ✅ Git repository cloning and checkout
- ✅ Automatic Dockerfile generation

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
    servers_count=1
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

**Python Services (Auto-Generated Dockerfile):**

```python
# Simple Flask/FastAPI app
project.add_python_service(
    "api",
    depends_on=["postgres", "redis"],  # Automatic startup order
    command="uvicorn main:app --host 0.0.0.0",
    port=8000,
    git_repo="https://github.com/user/myapp.git@main",
    git_token="tk8787",
    servers_count=3,
    domain="api.example.com",
    auto_scaling=True
)

# Worker service with multiple requirements files
project.add_python_service(
    "worker",
    depends_on=["redis"],
    command="python worker.py",
    requirements_files=["requirements.txt", "requirements-worker.txt"],
    build_context="/path/to/code",
    servers_count=2
)

# Gunicorn production setup
project.add_python_service(
    "api",
    python_version="3.11",
    command="gunicorn app:app --bind 0.0.0.0:8000 --workers 4",
    port=8000,
    build_context="/path/to/code",
    servers_count=3
)
```

**Node.js Services (Auto-Generated Dockerfile):**

```python
# Express API
project.add_nodejs_service(
    "api",
    depends_on=["postgres"],
    command="node server.js",
    port=3000,
    git_repo="https://github.com/user/api.git@main",
    git_token="tk8787",
    servers_count=3,
    domain="api.example.com"
)

# TypeScript app with build step
project.add_nodejs_service(
    "api",
    depends_on=["postgres"],
    build_command="npm run build",
    command="node dist/main.js",
    port=3000,
    git_repo="https://github.com/user/api.git@main",
    git_token="tk8787",
    servers_count=3
)

# Next.js application
project.add_nodejs_service(
    "web",
    build_command="npm run build",
    command="npm start",
    port=3000,
    git_repo="https://github.com/user/web.git@main",
    git_token="tk8787",
    servers_count=3
)

# Yarn-based project
project.add_nodejs_service(
    "api",
    package_manager="yarn",
    command="yarn start",
    port=3000,
    build_context="/path/to/code",
    servers_count=3
)
```

**React/Vue/Angular Websites (Auto-Generated Dockerfile):**

```python
# React SPA
project.add_react_service(
    "web",
    depends_on=["api"],
    git_repo="https://github.com/user/web.git@main",
    git_token="tk8787",
    domain="www.example.com",
    servers_count=3,
    auto_scaling=True
)

# Vue app
project.add_react_service(
    "web",
    build_dir="dist",  # Vue outputs to dist/
    git_repo="https://github.com/user/vue-app.git@main",
    git_token="tk8787",
    domain="www.example.com",
    servers_count=3
)

# Angular app
project.add_react_service(
    "web",
    build_dir="dist/myapp",  # Angular outputs to dist/project-name/
    build_command="npm run build -- --configuration production",
    domain="www.example.com"
)

# With custom nginx config (API proxy)
project.add_react_service(
    "web",
    nginx_config='''
        server {
            listen 80;
            root /usr/share/nginx/html;
            location / { try_files $uri $uri/ /index.html; }
            location /api { proxy_pass http://api:3000; }
        }
    ''',
    git_repo="https://github.com/user/web.git@main",
    git_token="tk8787",
    domain="www.example.com"
)
```

**Services from Git Repositories:**

```python
# From public GitHub repo (default branch)
project.add_service(
    "api",
    git_repo="https://github.com/user/myapp.git",
    git_token="tk8787",
    dockerfile="Dockerfile",
    servers_count=3
)

# From specific branch
project.add_service(
    "api",
    git_repo="https://github.com/user/myapp.git@develop",
    git_token="tk8787",
    dockerfile="Dockerfile",
    servers_count=3
)

# From specific tag (release)
project.add_service(
    "api",
    git_repo="https://github.com/user/myapp.git@v1.2.3",
    git_token="tk8787",
    dockerfile="Dockerfile",
    servers_count=3
)

# From specific commit
project.add_service(
    "api",
    git_repo="https://github.com/user/myapp.git@abc123def",
    git_token="tk8787",
    dockerfile="Dockerfile",
    servers_count=3
)

# From private repo (SSH)
project.add_service(
    "api",
    git_repo="git@github.com:user/private-repo.git@main",
    git_token="tk8787",
    dockerfile="Dockerfile",
    servers_count=3
)
```

**Git Checkout Behavior:**

- First build: Clones repository to `C:/local/git_checkouts/{project}/{env}/{service}/`
- Subsequent builds: Fetches updates and checks out specified ref
- Different environments can use different branches/tags
- Cleanup: `project.cleanup_git_checkouts()`

**Custom Services (Manual Dockerfile):**

```python
# From Dockerfile content
project.add_service(
    "api",
    dockerfile_content={
        "1": "FROM python:3.11-slim",
        "2": "WORKDIR /app",
        "3": "COPY requirements.txt .",
        "4": "RUN pip install -r requirements.txt",
        "5": "COPY . .",
        "6": "EXPOSE 8000",
        "7": "CMD ['python', 'app.py']"
    },
    build_context="/path/to/code",
    servers_count=3,
    domain="api.example.com"
)

# From existing Dockerfile
project.add_service(
    "worker",
    dockerfile="config/Dockerfile.worker",
    build_context="/path/to/code",
    servers_count=2
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
project.add_python_service(
    "cleanup_job",
    command="python cleanup.py",
    build_context="/path/to/code",
    schedule="0 2 * * *",  # 2 AM daily
    servers_count=1
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
project.add_python_service(
    "api",
    git_repo="https://github.com/user/api.git@main",
    git_token="tk8787",
    command="uvicorn main:app",
    port=8000,
    domain="api.example.com",
    server_zone="lon1",  # This service in London
    servers_count=3
)

project.add_python_service(
    "api-us",
    git_repo="https://github.com/user/api.git@main",
    git_token="tk8787",
    command="uvicorn main:app",
    port=8000,
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

### Service Dependencies

Control service startup order automatically using `depends_on`:

```python
# Create project
project = ProjectDeployer.create("myapp")

# Databases start first (no dependencies)
project.add_postgres() \
       .add_redis()

# API depends on databases (automatically gets startup_order=2)
project.add_python_service(
    "api",
    depends_on=["postgres", "redis"],
    command="uvicorn main:app",
    port=8000,
    servers_count=3
)

# Worker depends on API (automatically gets startup_order=3)
project.add_python_service(
    "worker",
    depends_on=["api", "redis"],
    command="python worker.py",
    servers_count=2
)

# Frontend depends on API (automatically gets startup_order=3)
project.add_react_service(
    "web",
    depends_on=["api"],
    domain="www.example.com"
)
```

**How it works:**

- Services with no dependencies default to `startup_order=1`
- When using `depends_on`, startup order is automatically calculated as max(dependencies) + 1
- Services with the same startup_order deploy in parallel
- You can still manually override with `startup_order` parameter if needed

**Manual startup_order (legacy, still supported):**

```python
project.add_postgres(startup_order=1)
project.add_service("api", startup_order=2, ...)
project.add_service("worker", startup_order=3, ...)
```

### Server Resources

Control server size per service:

```python
project.add_python_service(
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
project.add_python_service(
    "api",
    env_vars={
        "API_KEY": "secret",
        "DEBUG": "false",
        "DATABASE_URL": "auto"  # Auto-resolved via ResourceResolver
    }
)
```

### Volumes

```python
project.add_python_service(
    "api",
    volumes={
        "/local/myapp/prod/config/api": "/app/config:ro",  # Read-only
        "/local/myapp/prod/data/api": "/app/data"          # Read-write
    }
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

## Auto-Scaling

The system provides intelligent auto-scaling capabilities that automatically adjust your infrastructure based on real-time metrics.

### Overview

Auto-scaling monitors your services and can:

- **Vertical Scaling**: Upgrade/downgrade server specs (CPU/Memory) based on resource usage
- **Horizontal Scaling**: Add/remove servers based on request traffic (RPS)

**Key Features:**

- ✅ Automatic metric collection (CPU, Memory, RPS)
- ✅ Configurable thresholds per service
- ✅ Cooldown periods to prevent flapping
- ✅ Smart averaging over 10-minute windows
- ✅ Priority-based scaling (vertical first, then horizontal)

### Enabling Auto-Scaling

**Enable with defaults (both vertical and horizontal):**

```python
project.add_python_service(
    "api",
    command="uvicorn main:app",
    port=8000,
    servers_count=2,
    auto_scaling=True  # Enables both with default thresholds
)
```

**Custom thresholds:**

```python
project.add_python_service(
    "api",
    command="uvicorn main:app",
    port=8000,
    servers_count=2,
    auto_scaling={
        "vertical": {
            "cpu_scale_up": 80,      # Scale up when CPU > 80%
            "cpu_scale_down": 25,    # Scale down when CPU < 25%
            "memory_scale_up": 85,   # Scale up when Memory > 85%
            "memory_scale_down": 30  # Scale down when Memory < 30%
        },
        "horizontal": {
            "rps_scale_up": 1000,    # Add server when RPS > 1000
            "rps_scale_down": 100    # Remove server when RPS < 100
        }
    }
)
```

**Only vertical scaling:**

```python
project.add_python_service(
    "api",
    auto_scaling={
        "vertical": {
            "cpu_scale_up": 75,
            "memory_scale_up": 80
        }
    }
)
```

**Only horizontal scaling:**

```python
project.add_python_service(
    "api",
    auto_scaling={
        "horizontal": {
            "rps_scale_up": 500,
            "rps_scale_down": 50
        }
    }
)
```

### Default Thresholds

When `auto_scaling=True` or thresholds not specified:

**Vertical Scaling (Resource-based):**

- CPU scale up: 75%
- CPU scale down: 20%
- Memory scale up: 80%
- Memory scale down: 30%

**Horizontal Scaling (Traffic-based):**

- RPS scale up: 500 requests/second
- RPS scale down: 50 requests/second

### How It Works

**Architecture:**

1. **Metrics Collection** (Every 60 seconds):

   - All servers collect their own metrics
   - Metrics stored in rolling 10-minute window
   - CPU, Memory, and RPS tracked per service

2. **Scaling Decisions** (Every 5 minutes):

   - Leader server analyzes aggregated metrics
   - Averages across all servers running the service
   - Compares against configured thresholds
   - Makes scaling decision if needed

3. **Scaling Execution**:
   - Vertical: Updates server specs, redeploys service
   - Horizontal: Adds/removes servers, zero-downtime deployment

**Scaling Priority:**

Vertical scaling (resource optimization) takes priority over horizontal scaling (traffic handling). If both are needed, vertical scaling happens first, then horizontal on the next check cycle.

**Cooldown Periods:**

- Scale up: 5 minutes (react quickly to load spikes)
- Scale down: 10 minutes (be conservative to avoid flapping)

**Constraints:**

- Minimum servers: 1
- Maximum servers: 20
- Server size tiers follow DigitalOcean's available sizes (1-32 vCPU, 1GB-64GB RAM)

### Monitoring Auto-Scaling

**Check auto-scaling status:**

```python
from backend.infra.auto_scaling_coordinator import AutoScalingCoordinator

coordinator = AutoScalingCoordinator()

# Collect current metrics (happens automatically every minute)
coordinator.collect_all_metrics()

# Manual scaling check (happens automatically every 5 minutes via leader)
coordinator.check_and_scale_all_services()
```

**View scaling history in logs:**

```python
project.logs(service="api", env="prod", lines=200)
# Look for entries like:
# "Auto-scaling check for myapp/prod/api (3 servers)"
# "Metrics: CPU=82.3% Memory=65.1% RPS=723.4"
# "Triggering vertical scaling for api"
# "✓ Vertical scaling completed for api"
```

### Example Scenarios

**Scenario 1: High CPU usage**

```
Current: 2 servers, 2 vCPU each, CPU at 85%
Action: Vertical scale up to 4 vCPU per server
Result: 2 servers, 4 vCPU each, CPU drops to ~42%
```

**Scenario 2: Traffic spike**

```
Current: 3 servers, RPS at 650 per server (1950 total)
Action: Horizontal scale up, add 1 server
Result: 4 servers, RPS ~487 per server (1950 total distributed)
```

**Scenario 3: Low utilization**

```
Current: 5 servers, 4 vCPU each, CPU at 15%, RPS at 30 per server
Actions (over time):
1. Horizontal scale down: 5 → 4 servers (wait 10 min)
2. Horizontal scale down: 4 → 3 servers (wait 10 min)
3. Vertical scale down: 4 vCPU → 2 vCPU per server
```

### Cost Optimization

Auto-scaling helps optimize costs by:

- ✅ Scaling down during low-traffic periods
- ✅ Right-sizing server specs to actual usage
- ✅ Avoiding over-provisioning

**Example monthly savings:**

```
Without auto-scaling:
- 5 servers × 4 vCPU/8GB × $48/month = $240/month

With auto-scaling (average):
- 3 servers × 2 vCPU/4GB × $24/month = $72/month
- Scales up to 5×4vCPU during peak hours only
- Savings: ~$168/month (70%)
```

### Disabling Auto-Scaling

```python
# Remove auto-scaling from existing service
project.update_service("api", auto_scaling=False)
project.deploy(env="prod", service="api")
```

### Best Practices

**✅ Do:**

- Start with default thresholds and adjust based on observation
- Use vertical scaling for predictable workloads
- Use horizontal scaling for variable traffic patterns
- Monitor scaling decisions via logs
- Set reasonable min/max server counts for cost control

**❌ Don't:**

- Set thresholds too tight (causes flapping)
- Enable auto-scaling without monitoring
- Scale down too aggressively (can cause service degradation)
- Use auto-scaling for databases (use vertical scaling only if needed)

### Troubleshooting

**"Auto-scaling not triggering"**

- Check if service has `auto_scaling` enabled in config
- Verify metrics are being collected: check logs for "Auto-scaling check"
- Ensure leader server is healthy (oldest server becomes leader)
- Check if cooldown period is still active

**"Too many scale events"**

- Increase cooldown periods in `AutoScaler` class
- Widen threshold gaps (e.g., scale_up=80, scale_down=20 instead of 75/30)
- Increase metrics averaging window (default: 10 minutes)

**"Scaling in wrong direction"**

- Check metric collection: `coordinator.collect_all_metrics()`
- Review aggregated metrics in logs
- Verify thresholds are correctly configured
- Check if multiple services competing for resources

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

## File Synchronization

### Quick Sync with Batch Scripts

**Windows users can use convenience scripts:**

```bash
# Push config/secrets to all servers
push.bat

# Pull logs/data/backups from all servers
pull.bat
```

Both scripts automatically discover servers and prompt for project name.

### Manual Sync Operations

```python
from backend.infra.deployer import Deployer

deployer = Deployer("myapp")

# Push local files to servers
deployer.push_config(env="prod")

# Pull server data to local
deployer.pull_data(env="prod")

# Full bidirectional sync
deployer.full_sync(env="prod")
```

### Directory Structure

**Local (Windows: `C:/local/{project}/{env}/`, Linux: `/local/{project}/{env}/`):**

```
├── config/     # Service configs (PUSH →)
├── secrets/    # Passwords, certs (PUSH →)
├── files/      # Static assets (PUSH →)
├── data/       # Database files (← PULL, per-server)
├── logs/       # App logs (← PULL, per-server)
├── backups/    # DB backups (← PULL, per-server)
└── monitoring/ # Metrics (← PULL, per-server)
```

**Sync Behavior:**

- **Push**: Single archive, parallel to all servers, auto-distributes secrets
- **Pull**: Server-separated (e.g., `logs/192_168_1_100/`), parallel retrieval
- **Automatic**: Secrets from databases copied to all consumer services

### Common Workflows

```python
# 1. Config change workflow
# Edit: C:/local/myapp/prod/config/api/settings.json
deployer.push_config(env="prod")
project.deploy(env="prod", service="api", build=False)

# 2. Retrieve backups
deployer.pull_data(env="prod")
# Backups in: C:/local/myapp/prod/backups/{server_ip}/

# 3. Analyze logs
deployer.pull_data(env="prod")
# Logs in: C:/local/myapp/prod/logs/{server_ip}/
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

**6. "Git checkout failed"**

- Ensure Git is installed on your system
- Verify repository URL is correct
- For private repos, ensure SSH keys are configured
- Check network connectivity to Git hosting service

**7. "Requirements file not found"**

- Ensure requirements files exist in build_context or git_repo
- Check file paths are relative to repository root
- Verify files are committed to Git repository

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

# Check Git checkouts
import os
git_path = "C:/local/git_checkouts/myapp/prod/" if os.name == 'nt' else "/local/git_checkouts/myapp/prod/"
if os.path.exists(git_path):
    print(f"Git checkouts: {os.listdir(git_path)}")
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
- `GitManager` - Git repository checkout and management

---
