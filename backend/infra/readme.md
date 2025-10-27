# ProjectDeployer - Multi-Tenant Docker Deployment System

A production-grade Python deployment system that automates Docker container orchestration across multiple DigitalOcean regions with user segregation, zero-downtime deployments, automatic SSL, health monitoring, and backups.

## Table of Contents

- [Quick Start](#quick-start)
- [Architecture Overview](#architecture-overview)
- [Multi-Tenancy & User Segregation](#multi-tenancy--user-segregation)
- [Prerequisites](#prerequisites)
- [Basic Usage](#basic-usage)
- [Service Configuration](#service-configuration)
- [Multi-Zone Deployments](#multi-zone-deployments)
- [SSL & Domain Management](#ssl--domain-management)
- [Database Access](#database-access)
- [Health Monitoring](#health-monitoring)
- [Backups & Restore](#backups--restore)
- [File Synchronization](#file-synchronization)
- [Auto-Scaling](#auto-scaling)
- [Troubleshooting](#troubleshooting)
- [Security & Limitations](#security--limitations)
- [API Reference](#api-reference)

---

## Quick Start

### 1. Create and Deploy a Project

```python
from backend.infra.project_deployer import ProjectDeployer

# Create new project (user ID must be explicitly provided)
project = ProjectDeployer.create("u1", "myapp", docker_hub_user="myusername")

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
           servers_count=3
       )

# Deploy to production
project.deploy(env="prod")
```

### 2. Manage Deployments

```python
# Load existing project (with explicit user ID)
project = ProjectDeployer("u1", "myapp")

# Check status
project.status()

# View logs with error highlighting
project.logs(service="api", env="prod", lines=100)

# Rollback if needed
project.rollback(env="prod", service="api")
```

---

## Architecture Overview

### Multi-Tenant Architecture with User Segregation

The system provides **complete isolation** between different users through a user-based directory structure:

```
/local/
├── u1/                           # User 1's isolated space
│   ├── myapp/
│   │   ├── prod/
│   │   │   ├── config/           # Service configurations
│   │   │   ├── secrets/          # Passwords, certificates
│   │   │   ├── files/            # Static assets
│   │   │   ├── data/             # Database files (per-server)
│   │   │   ├── logs/             # Application logs (per-server)
│   │   │   └── backups/          # Database backups (per-server)
│   │   └── uat/
│   └── otherapp/
└── u2/                           # User 2's completely separate space
    └── theirapp/
```

**Key Benefits:**

- ✅ **Complete isolation** - Users cannot access each other's data
- ✅ **Resource naming** - All containers, volumes, networks include user prefix (e.g., `u1_myapp_prod_api`)
- ✅ **Port segregation** - Ports generated from hash include user ID
- ✅ **Secrets isolation** - Each user has separate credential directories
- ✅ **Clean multi-tenancy** - Multiple users can deploy the same project name without conflicts

### Temporary Build Isolation

Build operations use isolated temporary directories to prevent cross-contamination:

**Windows:** `C:\Users\{username}\AppData\Local\Temp\deployment_infra\`
**Linux:** `/tmp/deployment_infra/`

```
/tmp/deployment_infra/
├── dockerfiles/
│   └── u1/
│       ├── Dockerfile.myapp-prod-api.tmp        # Temporary Dockerfiles
│       └── Dockerfile.myapp-prod-worker.tmp
├── build_contexts/
│   └── u1/
│       ├── myapp-prod-api/                      # Isolated build contexts
│       └── myapp-prod-worker/
└── git_repos/
    └── u1/
        └── myapp-api-{hash}/                     # Temporary Git clones
```

**Automatic Cleanup:**

- Temporary Dockerfiles removed after build completes
- Git clones cleaned up after image build
- Build contexts preserved for debugging (optional cleanup)
- All temp files properly segregated by user ID

### Three-Tier Port System

The system uses a sophisticated port architecture for zero-downtime deployments:

1. **Container Port** (Fixed)

   - Internal to container (e.g., Postgres: 5432, Redis: 6379)
   - Never exposed to host

2. **Host Port** (Toggles)

   - Base: `hash(user_project_env_service_port) % 1000 + 8000`
   - Secondary: `base + 10000`
   - Range: 8000-8999 (base), 18000-18999 (secondary)
   - Alternates each deployment for zero-downtime

3. **Internal Port** (Stable)
   - Generated: `hash(user_project_env_service_internal) % 1000 + 5000`
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

1. **Image Building** - Docker images built in isolated temp directories
2. **Startup Order Groups** - Services deploy in order (databases first)
3. **Server Allocation** - Reuses existing servers, creates new as needed
4. **Parallel Deployment** - Multiple servers deploy simultaneously
5. **Toggle Deployment** - New container runs alongside old
6. **Health Check** - Verify new container is healthy (with detailed error logging)
7. **Nginx Update** - Update all servers' nginx configs
8. **Traffic Switch** - Nginx redirects to new container
9. **Cleanup** - Remove old container
10. **Server Cleanup** - Destroy unused servers, keep active ones

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

## Multi-Tenancy & User Segregation

### User ID System

Every deployment operation requires a user ID that ensures complete isolation. The user ID must be **explicitly provided** as the first parameter:

```python
from backend.infra.project_deployer import ProjectDeployer

# User ID must be explicitly passed
user_id = "u1"  # Your user identifier (e.g., "u1", "u2", "alice", etc.)
project = ProjectDeployer.create(user_id, "myapp", docker_hub_user="myusername")

# Load existing project
project = ProjectDeployer(user_id, "myapp")
```

**User ID Conventions:**

- Use short, alphanumeric identifiers (e.g., "u1", "u2", "alice")
- Must be consistent across all operations
- Each user gets completely isolated resources
- Cannot be changed after project creation

### Resource Naming Convention

All resources include the user ID prefix:

**Container Names:**

```
u1_myapp_prod_api           # User 1's production API
u1_myapp_prod_api_secondary # Toggle deployment variant
u2_myapp_prod_api           # User 2's completely separate API
```

**Volume Names:**

```
u1_myapp_prod_data_postgres
u1_myapp_prod_logs_api
```

**Network Names:**

```
deployer_network            # Shared Docker network (isolated by user containers)
```

**Directory Structure:**

```
/local/u1/myapp/prod/       # User 1's data
/local/u2/myapp/prod/       # User 2's data (completely separate)
```

### Port Isolation

Ports are generated using a hash that includes the user ID:

```python
def get_host_port(user, project, env, service, container_port):
    hash_input = f"{user}_{project}_{env}_{service}_{container_port}"
    return (hash(hash_input) % 1000) + 8000

# Examples:
get_host_port("u1", "myapp", "prod", "api", "8000")  # → 8357
get_host_port("u2", "myapp", "prod", "api", "8000")  # → 8821 (different!)
```

This ensures that even if two users deploy the same project/service, they get different ports with zero collision risk.

### Security Boundaries

**User Isolation:**

- ✅ File system paths segregated by user ID
- ✅ Container names include user prefix
- ✅ Ports derived from user-specific hashes
- ✅ Secrets directories completely separate
- ✅ Docker volumes namespaced by user

**Server Sharing:**

- Servers are tagged with project/env but can be shared across users
- Container-level isolation prevents cross-user access
- Network isolation via Docker networks
- SSH key management per-user basis

---

## Prerequisites

### Required Environment Variables

Create a `.env` file in the project root:

```bash
# Required for remote deployments
DIGITALOCEAN_API_TOKEN=your_do_token_here

# Optional - set custom user ID (defaults to system username)
DEPLOYMENT_USER_ID=u1

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
- ✅ SSH key generation and deployment (per-user)
- ✅ VPC network setup (per region)
- ✅ Firewall configuration
- ✅ SSL certificates (Let's Encrypt or self-signed)
- ✅ Health monitoring installation
- ✅ Nginx sidecar for service mesh
- ✅ Git repository cloning (in isolated temp directories)
- ✅ Automatic Dockerfile generation
- ✅ User-segregated directory structures

---

## Basic Usage

### Creating a Project

```python
# Create with defaults (must provide user ID)
project = ProjectDeployer.create("u1", "myapp")

# Create with Docker Hub user
project = ProjectDeployer.create("u1", "myapp", docker_hub_user="username")

# Create with custom defaults
project = ProjectDeployer.create(
    "u1",
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
    depends_on=["postgres", "redis"],
    command="uvicorn main:app --host 0.0.0.0",
    port=8000,
    git_repo="https://github.com/user/myapp.git@main",
    git_token="tk8787",
    servers_count=3,
    domain="api.example.com",
    auto_scaling=True
)

# Worker service with schedule
project.add_python_service(
    "worker",
    depends_on=["redis"],
    command="python worker.py",
    build_context="/path/to/code",
    schedule="*/5 * * * *",  # Every 5 minutes
    servers_count=2
)
```

**Node.js Services:**

```python
project.add_nodejs_service(
    "backend",
    depends_on=["postgres"],
    command="node server.js",
    port=3000,
    git_repo="https://github.com/user/backend.git@main",
    node_version="18",
    package_manager="yarn",
    servers_count=2
)
```

**React/SPA Services:**

```python
project.add_react_service(
    "web",
    depends_on=["api"],
    git_repo="https://github.com/user/frontend.git@main",
    build_command="npm run build",
    domain="www.example.com",
    servers_count=3
)
```

**Custom Dockerfile:**

```python
# From dockerfile_content
project.add_service(
    "custom",
    dockerfile_content={
        "1": "FROM python:3.11-slim",
        "2": "WORKDIR /app",
        "3": "COPY requirements.txt .",
        "4": "RUN pip install -r requirements.txt",
        "5": "COPY . .",
        "6": "EXPOSE 8000",
        "7": "CMD ['python', '-u', 'app.py']"  # -u for unbuffered output (better logging)
    },
    build_context="/path/to/code",
    servers_count=2
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

# Deploy to multiple zones
project.deploy(env="prod", zones=["lon1", "nyc3", "sgp1"])
```

---

## Service Configuration

### Service Dependencies

Control service startup order automatically using `depends_on`:

```python
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
```

### Server Resources

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
        "DEBUG": "false"
    }
)
```

### Using ResourceResolver for Service Discovery

Your application code should use `ResourceResolver` to connect to services:

```python
from shared_libs.backend.infra.resource_resolver import ResourceResolver

# User ID, project, and environment must match your deployment
user = "u1"  # Must match the user ID used in deployment
project = "myapp"
env = "prod"

# Connect to PostgreSQL
db_config = {
    "host": ResourceResolver.get_service_host(user, project, env, "postgres"),
    "port": ResourceResolver.get_service_port(user, project, env, "postgres"),
    "database": ResourceResolver.get_db_name(user, project, env, "postgres"),
    "user": ResourceResolver.get_db_user(user, project, env, "postgres"),
    "password": ResourceResolver.get_service_password(user, project, env, "postgres")
}

# Connect to Redis
redis_url = ResourceResolver.get_redis_connection_string(user, project, env)
```

**Important:** All `ResourceResolver` methods require the `user` parameter as the first argument. This must match the user ID used when creating/deploying the project.

---

## Multi-Zone Deployments

Deploy your application across multiple geographic regions with automatic load balancing.

### Requirements

1. **Cloudflare Account** with Load Balancer enabled ($5/month)
2. **CLOUDFLARE_API_TOKEN** environment variable
3. **Domain configured** for services

### Configuration

```python
# Deploy to multiple zones explicitly
project.deploy(env="prod", zones=["lon1", "nyc3", "sgp1"])

# Or configure zones per service
project.add_python_service(
    "api",
    server_zone="lon1",
    servers_count=3,
    domain="api.example.com"
)
```

### How Multi-Zone Works

1. **Parallel Deployment** - Each zone deploys simultaneously
2. **Health Checks** - Verify all zones are healthy
3. **Cloudflare Load Balancer** - Automatically configured with:
   - Health monitoring (HTTPS checks every 60s)
   - Geo-steering (routes users to nearest zone)
   - Automatic failover (removes unhealthy origins)
4. **DNS Management** - Cloudflare handles routing

---

## Health Monitoring

### Architecture: Distributed Monitoring + HTTP Agent

**Two Components Work Together:**

1. **Health Monitor** (Cron Job)

   - Runs every minute on all servers
   - Detects failures and coordinates healing
   - Only leader server performs actions

2. **Health Agent** (HTTP API Service)
   - Always-running systemd service
   - Port 9999 (VPC-only, secured with API key)
   - Provides HTTP API for container management
   - Lightweight Flask + subprocess (10MB)

**Health Check Features:**

**Advanced Container Health Verification:**

- Multi-method log retrieval (7 different strategies)
- Intelligent error detection and highlighting
- Noise filtering (build output, package installation)
- Error-first logging (shows ❌ critical errors first)
- Container state analysis (running/restarting/exited/unhealthy)
- Automatic retry with exponential backoff
- Startup grace periods for slow-starting containers

**Error Logging Improvements:**

```
[server] ⚠️  ERROR LINES DETECTED:
[server] ❌ Traceback (most recent call last):
[server] ❌   File "/app/api.py", line 30, in <module>
[server] ❌ TypeError: ResourceResolver.get_service_host() missing 1 required positional argument
```

**What It Monitors:**

1. **Server Health:**

   - Ping connectivity (ICMP)
   - Docker daemon status
   - Container presence
   - Disk space

2. **Service Health:**

   - Container running status
   - Expected vs actual containers
   - Container restart loops
   - Application startup errors

3. **Cross-Server Checks:**
   - All servers monitor each other via VPC
   - Leader coordinates healing actions
   - Automatic failover

### Self-Healing

**Stage 1: Container Restart (Fast - 10 seconds)**

- Leader detects missing container
- Restarts via health agent API
- Verification

**Stage 2: Server Replacement (Full - 5 minutes)**

- Provisions new server
- Pushes config/secrets
- Deploys services
- Health check
- Destroys failed server

---

## Backups & Restore

### Automatic Backups

Backups are scheduled automatically for stateful services:

```python
# PostgreSQL - daily at 2 AM
project.add_postgres()  # Backup schedule auto-configured

# Redis - daily at 3 AM
project.add_redis()  # Backup schedule auto-configured
```

### Manual Backup Operations

```python
# Trigger immediate backup
project.backup(env="prod", service="postgres")

# List available backups
backups = project.list_backups(env="prod", service="postgres")

# Restore from backup
project.restore(
    env="prod",
    service="postgres",
    backup_file="postgres_backup_20240115_020000.sql"
)
```

### Backup Storage

Backups are stored in user-segregated directories:

**Local:**

- Windows: `C:/local/{user}/{project}/{env}/backups/`
- Linux: `/local/{user}/{project}/{env}/backups/`

**Remote:**

- `/local/{user}/{project}/{env}/backups/{server_ip}/`

Backups are automatically pulled to local storage during sync operations.

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

### Manual Sync Operations

```python
project = ProjectDeployer("u1", "myapp")

# Push local files to servers
project.push_config(env="prod")

# Pull server data to local
project.pull_data(env="prod")

# Full bidirectional sync
project.sync_files(env="prod")
```

### Directory Structure

**Local Structure:**

Windows: `C:/local/{user}/{project}/{env}/`
Linux: `/local/{user}/{project}/{env}/`

```
├── config/     # Service configs (PUSH →)
├── secrets/    # Passwords, certs (PUSH →)
├── files/      # Static assets (PUSH →)
├── data/       # Database files (← PULL, per-server)
├── logs/       # App logs (← PULL, per-server)
├── backups/    # DB backups (← PULL, per-server)
└── monitoring/ # Metrics (← PULL, per-server)
```

**Server Structure:**

`/local/{user}/{project}/{env}/`

Same structure as local, but data/logs/backups/monitoring are server-specific.

---

## Auto-Scaling

### Enabling Auto-Scaling

```python
project.add_python_service(
    "api",
    command="uvicorn main:app",
    port=8000,
    servers_count=2,
    auto_scaling=True  # Enables both vertical and horizontal
)

# Custom thresholds
project.add_python_service(
    "api",
    auto_scaling={
        "vertical": {
            "cpu_scale_up": 80,
            "cpu_scale_down": 25,
            "memory_scale_up": 85,
            "memory_scale_down": 30
        },
        "horizontal": {
            "rps_scale_up": 1000,
            "rps_scale_down": 100
        }
    }
)
```

### How It Works

1. **Metrics Collection** (Every 60 seconds)
2. **Scaling Decisions** (Every 5 minutes)
3. **Scaling Execution** (Zero-downtime)

**Default Thresholds:**

- CPU scale up: 75% / down: 20%
- Memory scale up: 80% / down: 30%
- RPS scale up: 500 / down: 50

---

## Troubleshooting

### Common Issues

**1. "No logs available"**

This typically means the container crashed before producing output. The system now tries 7 different methods to retrieve logs:

- Standard docker logs
- Logs with timestamps
- Logs since container start
- Direct log file access
- Container exec to check error files
- Historical container instances
- System journal logs

**2. "Health check failed"**

The health check now provides detailed error output:

- Container state (running/restarting/exited)
- Exit code
- Full error logs with highlighting
- Specific error patterns detected

Check the deployment logs for ❌ marked error lines showing the exact problem.

**3. "Container in bad state: Restarting"**

Your application is crashing immediately. Common causes:

- Missing environment variables
- Import errors (wrong user parameter in ResourceResolver)
- Missing dependencies
- Syntax errors in code

The deployment logs will show the exact Python traceback or error message.

**4. "Port conflict"**

Extremely rare due to user-based port hashing. If it occurs:

- Check if another user is using the same port manually
- Verify user ID is correctly set
- Check for hardcoded ports in config

**5. "Git checkout failed"**

- Ensure Git is installed
- Verify repository URL and token
- Check network connectivity
- Verify branch/tag exists

**6. "Build context not found"**

The build context path must exist and contain your code:

```python
build_context="/path/to/code"  # Must exist
```

Git repositories are automatically cloned to temp directories.

### Debug Mode

```python
# Enable verbose logging
import logging
logging.basicConfig(level=logging.DEBUG)

# Load project with user ID
project = ProjectDeployer("u1", "myapp")

# Check deployment state
state = project.get_deployment_state(env="prod", service="api")
print(state)

# Get detailed server info
servers = project.list_servers(env="prod")
for s in servers:
    print(f"{s['ip']}: {s['deployment_status']} - {s['zone']}")
```

### Improved Error Reporting

The system now includes:

- **Multi-method log retrieval** - tries 7 different ways to get logs
- **Error highlighting** - ❌ markers on critical errors
- **Noise filtering** - removes build output and package installation spam
- **Traceback detection** - automatically highlights Python errors
- **Container state analysis** - detailed diagnosis of container health
- **Exit code reporting** - shows why containers crashed

---

## Security & Limitations

### Multi-Tenancy Security

**Isolation Levels:**

✅ **Strong Isolation:**

- File system paths (complete separation)
- Container names (user-prefixed)
- Volume names (user-prefixed)
- Secrets directories (separate per-user)
- Port allocation (hash includes user ID)

⚠️ **Shared Resources:**

- Physical servers (container-level isolation only)
- Docker daemon (shared but isolated via namespaces)
- VPC network (filtered by container names)

### Bastion Server Security Risk

**CRITICAL LIMITATION:**

The deployment system requires a **bastion server** (your local machine or a dedicated deployment server) that has SSH access to all production servers. This creates a significant security risk:

**Risk:** If the bastion server is compromised, an attacker can:

- SSH into all production servers
- Access all user data across all tenants
- Read secrets and credentials
- Modify or destroy containers
- Exfiltrate database backups
- Pivot to other infrastructure

**Mitigation Strategies:**

1. **Bastion Hardening:**

   - Keep bastion offline when not deploying
   - Use hardware security key for SSH
   - Enable full disk encryption
   - Regular security audits
   - Minimal software installation
   - No browsing/email on bastion

2. **SSH Key Management:**

   - Use separate SSH keys per environment
   - Rotate SSH keys regularly
   - Store keys in hardware security modules (HSM)
   - Never commit keys to version control
   - Use ssh-agent forwarding instead of copying keys

3. **Network Isolation:**

   - Bastion in separate VLAN/VPC
   - Firewall rules limiting bastion access
   - VPN required to reach bastion
   - IP whitelisting for bastion connections
   - Monitor all SSH connections from bastion

4. **Access Control:**

   - Multi-factor authentication for bastion
   - Time-limited access tokens
   - Audit all bastion access
   - Principle of least privilege
   - Separate bastions per environment (dev/prod)

5. **Alternative Architecture (Future Enhancement):**
   - **Agent-based deployment** - Servers pull configs instead of bastion pushing
   - **Zero-trust networking** - Mutual TLS between all components
   - **Secrets management service** - HashiCorp Vault or similar
   - **Ephemeral bastion** - Temporary bastion created per-deployment and destroyed

**Best Practice:** Treat the bastion server as your **most critical security asset**. Its compromise means complete infrastructure compromise across all users.

### Additional Security Considerations

**Container Security:**

- Containers run as root by default (can be overridden)
- Docker socket not exposed to containers
- Network policies via Docker networks
- Read-only mounts for configs/secrets

**Secret Management:**

- Secrets stored on filesystem (not in environment vars)
- Mounted read-only into containers
- Separate secrets directory per user
- Automatic secret rotation supported

**Network Security:**

- VPC network for inter-server communication
- Firewall rules auto-configured
- Only necessary ports exposed
- SSL/TLS for external traffic

### Known Limitations

1. **Bastion Compromise Risk** - Single point of failure (see above)
2. **Shared Docker Daemon** - Users share the same Docker daemon on servers
3. **No Pod-Level Isolation** - Unlike Kubernetes, no strong pod isolation
4. **File System Permissions** - Relies on Unix permissions (not SELinux/AppArmor)
5. **No Network Policies** - Basic Docker network isolation only
6. **Limited RBAC** - No fine-grained role-based access control
7. **Server Sharing** - Multiple users can have containers on same server

### Compliance Considerations

This system may **not** be suitable for:

- PCI-DSS Level 1 (payment card data)
- HIPAA (healthcare data) without additional controls
- FedRAMP (government cloud services)
- High-security government workloads

Consider Kubernetes with proper RBAC, network policies, and pod security policies for these use cases.

---

## API Reference

**Key Classes:**

- `ProjectDeployer` - Unified interface for ALL operations (single import needed)
- `ResourceResolver` - Service discovery and connection details (automatic)
- `GitManager` - Git repository checkout and management (automatic)

**Note:** You no longer need to import `Deployer`, `SecretsRotator`, `HealthMonitor`, `ServerInventory`, or `DeploymentStateManager` directly. All functionality is accessible through `ProjectDeployer`.

### class `ProjectDeployer`

Primary interface for deployment operations.

<details>
<summary><strong>Service Management Methods</strong></summary>

| Method               | Args                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | Returns           | Description                                                                                          |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------- | ---------------------------------------------------------------------------------------------------- |
| `create`             | `user: str`, `project: str`, `docker_hub_user: str=None`, `version: str="latest"`, `default_server_ip: str="localhost"`                                                                                                                                                                                                                                                                                                                                                                                                                                                          | `ProjectDeployer` | Create a new project with default configuration. Static method. User ID must be provided explicitly. |
| `add_service`        | `name: str`, `image: str=None`, `dockerfile: str=None`, `dockerfile_content: Dict=None`, `build_context: str=None`, `git_repo: str=None`, `git_token: str=None`, `command: str=None`, `ports: List[int]=None`, `env_vars: Dict=None`, `volumes: Dict=None`, `depends_on: List[str]=None`, `servers_count: int=1`, `server_zone: str="lon1"`, `server_cpu: int=1`, `server_memory: int=1024`, `domain: str=None`, `ssl_mode: str=None`, `schedule: str=None`, `health_check: bool=True`, `restart: bool=True`, `startup_order: int=None`, `auto_scaling: Union[bool, Dict]=False` | `ProjectDeployer` | Add a generic service to the project. Returns self for chaining.                                     |
| `add_postgres`       | `version: str="15"`, `servers_count: int=1`, `server_zone: str="lon1"`, `**kwargs`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | `ProjectDeployer` | Add PostgreSQL database service.                                                                     |
| `add_redis`          | `version: str="7-alpine"`, `servers_count: int=1`, `server_zone: str="lon1"`, `**kwargs`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | `ProjectDeployer` | Add Redis cache service.                                                                             |
| `add_opensearch`     | `version: str="2"`, `servers_count: int=1`, `server_zone: str="lon1"`, `**kwargs`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | `ProjectDeployer` | Add OpenSearch service.                                                                              |
| `add_python_service` | `name: str`, `command: str`, `port: int=None`, `python_version: str="3.11"`, `requirements_files: List[str]=["requirements.txt"]`, `depends_on: List[str]=None`, `build_context: str=None`, `git_repo: str=None`, `git_token: str=None`, `servers_count: int=1`, `server_zone: str="lon1"`, `domain: str=None`, `schedule: str=None`, `auto_scaling: Union[bool, Dict]=False`, `**kwargs`                                                                                                                                                                                        | `ProjectDeployer` | Add Python service with auto-generated Dockerfile.                                                   |
| `add_nodejs_service` | `name: str`, `command: str`, `port: int=None`, `node_version: str="18"`, `package_manager: str="npm"`, `build_command: str=None`, `depends_on: List[str]=None`, `build_context: str=None`, `git_repo: str=None`, `git_token: str=None`, `servers_count: int=1`, `server_zone: str="lon1"`, `domain: str=None`, `auto_scaling: Union[bool, Dict]=False`, `**kwargs`                                                                                                                                                                                                               | `ProjectDeployer` | Add Node.js service with auto-generated Dockerfile.                                                  |
| `add_react_service`  | `name: str`, `node_version: str="18"`, `package_manager: str="npm"`, `build_command: str="npm run build"`, `output_dir: str="build"`, `nginx_config: str=None`, `depends_on: List[str]=None`, `build_context: str=None`, `git_repo: str=None`, `git_token: str=None`, `servers_count: int=1`, `server_zone: str="lon1"`, `domain: str=None`, `**kwargs`                                                                                                                                                                                                                          | `ProjectDeployer` | Add React/SPA service with multi-stage Dockerfile (build + nginx).                                   |
| `update_service`     | `name: str`, `**kwargs`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | `ProjectDeployer` | Update existing service configuration.                                                               |
| `delete_service`     | `name: str`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | `ProjectDeployer` | Remove service from configuration.                                                                   |
| `build`              | `env: str=None`, `service: str=None`, `push: bool=True`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | `bool`            | Build Docker images (in isolated temp directories).                                                  |
| `deploy`             | `env: str=None`, `service: str=None`, `zones: List[str]=None`, `build: bool=True`, `parallel: bool=True`                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | `bool`            | Deploy services to servers.                                                                          |
| `rollback`           | `env: str`, `service: str`, `to_version: str=None`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | `bool`            | Rollback service to previous version.                                                                |
| `status`             | `env: str=None`, `service: str=None`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | `Dict`            | Get deployment status.                                                                               |
| `logs`               | `service: str`, `env: str`, `lines: int=100`, `follow: bool=False`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | `str`             | View service logs (with error highlighting).                                                         |

</details>

<details>
<summary><strong>File Operations Methods</strong></summary>

| Method         | Args                                                            | Returns      | Description                       |
| -------------- | --------------------------------------------------------------- | ------------ | --------------------------------- |
| `push_config`  | `env: str=None`, `targets: List[str]=None`                      | `bool`       | Push config/secrets to servers.   |
| `pull_data`    | `env: str=None`, `targets: List[str]=None`                      | `bool`       | Pull logs/backups from servers.   |
| `pull_backups` | `env: str=None`, `service: str=None`, `targets: List[str]=None` | `bool`       | Pull backups specifically.        |
| `sync_files`   | `env: str=None`, `targets: List[str]=None`                      | `bool`       | Bidirectional sync (push + pull). |
| `list_backups` | `env: str`, `service: str`                                      | `List[Dict]` | List available backups.           |

</details>

<details>
<summary><strong>Secrets Management Methods</strong></summary>

| Method           | Args                                                             | Returns     | Description                                   |
| ---------------- | ---------------------------------------------------------------- | ----------- | --------------------------------------------- |
| `rotate_secrets` | `env: str`, `services: List[str]=None`, `auto_deploy: bool=True` | `bool`      | Rotate passwords (with optional auto-deploy). |
| `list_secrets`   | `env: str`                                                       | `List[str]` | List all secrets.                             |

</details>

<details>
<summary><strong>Health & Monitoring Methods</strong></summary>

| Method              | Args            | Returns | Description                               |
| ------------------- | --------------- | ------- | ----------------------------------------- |
| `check_health`      | `env: str=None` | `Dict`  | Run health check once.                    |
| `get_health_status` | `env: str=None` | `Dict`  | Get current health status of all servers. |

</details>

<details>
<summary><strong>Server Management Methods</strong></summary>

| Method                   | Args                                                  | Returns      | Description                    |
| ------------------------ | ----------------------------------------------------- | ------------ | ------------------------------ |
| `list_servers`           | `env: str=None`, `zone: str=None`, `status: str=None` | `List[Dict]` | List servers (with filtering). |
| `destroy_server`         | `server_ip: str`                                      | `bool`       | Destroy specific server.       |
| `get_deployment_state`   | `env: str`, `service: str`                            | `Dict`       | Get current deployment state.  |
| `get_deployment_history` | `env: str`, `service: str`, `limit: int=10`           | `List[Dict]` | Get deployment history.        |

</details>

---

### class `ResourceResolver`

Service discovery and connection management (used within application code).

<details>
<summary><strong>Public Methods</strong></summary>

| Method                           | Args                                                                                                     | Returns | Category          | Description                                                          |
| -------------------------------- | -------------------------------------------------------------------------------------------------------- | ------- | ----------------- | -------------------------------------------------------------------- |
| `get_service_host`               | `user: str`, `project: str`, `env: str`, `service: str`                                                  | `str`   | Service Discovery | Get hostname for service (returns "nginx" for service mesh routing). |
| `get_service_port`               | `user: str`, `project: str`, `env: str`, `service: str`                                                  | `int`   | Service Discovery | Get internal port for service (stable, hash-based).                  |
| `get_service_password`           | `user: str`, `project: str`, `env: str`, `service: str`                                                  | `str`   | Credentials       | Get service password from secrets.                                   |
| `get_db_name`                    | `user: str`, `project: str`, `env: str`, `service: str`                                                  | `str`   | Database          | Get database name.                                                   |
| `get_db_user`                    | `user: str`, `project: str`, `service: str`                                                              | `str`   | Database          | Get database user.                                                   |
| `get_postgres_connection_string` | `user: str`, `project: str`, `env: str`, `service: str="postgres"`                                       | `str`   | Database          | Get complete PostgreSQL connection string.                           |
| `get_redis_connection_string`    | `user: str`, `project: str`, `env: str`, `service: str="redis"`, `db: int=0`                             | `str`   | Database          | Get complete Redis connection string.                                |
| `get_container_name`             | `user: str`, `project: str`, `env: str`, `service: str`                                                  | `str`   | Naming            | Get container name (includes user prefix).                           |
| `get_image_name`                 | `docker_hub_user: str`, `user: str`, `project: str`, `env: str`, `service: str`, `version: str="latest"` | `str`   | Naming            | Get Docker image name.                                               |
| `detect_target_os`               | `server_ip: str=None`, `user: str="root"`                                                                | `str`   | System            | Detect OS of target server ("windows" or "linux").                   |

</details>

---

## Contributing

Contributions are welcome! Please ensure:

- User segregation is maintained in all new features
- Temporary files use the isolated temp directory structure
- Error logging includes detailed diagnostics
- Health checks handle edge cases gracefully
- Documentation is updated for user-visible changes

---

## License

[Your License Here]

---

## Support

For issues, questions, or feature requests:

- GitHub Issues: [Your Repo]
- Documentation: [Your Docs URL]
- Email: [Support Email]
