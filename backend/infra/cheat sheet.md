# Quick Start: Create a New Project

## 0. Prerequisites

### Local Machine

- **Docker installed and running** (required for all operations)
- **Python 3.8+** with required packages
- **DigitalOcean account** (for remote deployments)

### What Gets Automated

The deployer automatically handles:

- ✅ Server provisioning via DigitalOcean API
- ✅ Docker installation on all servers
- ✅ SSH key generation and deployment
- ✅ VPC networking setup (per region)
- ✅ Firewall configuration
- ✅ SSL certificates (Let's Encrypt or self-signed)

**You only need:** Docker Desktop + DO API token. Everything else is automatic.

## 1. Setup Environment Variables (One-Time)

Create `.env` in root (add to `.gitignore`):

```env
DIGITALOCEAN_API_TOKEN=your_do_token_here
CLOUDFLARE_API_TOKEN=your_cf_token_here  # Optional - see SSL scenarios below
CLOUDFLARE_EMAIL=your@email.com          # Optional - see SSL scenarios below
ADMIN_IP=auto
```

### SSL/HTTPS Scenarios

The deployer automatically configures SSL based on deployment target:

**Localhost (Development)**

- **Result:** Self-signed certificate (browser warning expected for HTTPS testing)
- **Requirements:** None - works out of the box

**Remote + Email Only (Production - Basic)**

```env
DIGITALOCEAN_API_TOKEN=your_token
CLOUDFLARE_EMAIL=your@email.com # if no email, a bogus address is used
```

- **Result:** Let's Encrypt standalone (publicly trusted HTTPS, no browser warnings)
- **Limitations:** 5-10 second downtime during cert issuance, no wildcard certificates

**Remote + Cloudflare (Production - Full Automation)**

```env
DIGITALOCEAN_API_TOKEN=your_token
CLOUDFLARE_EMAIL=your@email.com
CLOUDFLARE_API_TOKEN=your_cf_token
```

- **Result:** Let's Encrypt DNS-01 + Cloudflare CDN/DDoS protection
- **Benefits:** Zero downtime, wildcard certificates, automatic DNS, CDN protection

**Recommendation:** Use Cloudflare scenario for production.

## 2. Create Project Config

`config/projects/<your_project_name>.json`:

```json
{
  "project": {
    "name": "your_project",
    "docker_hub_user": "your_dockerhub_username",
    "services": {
      "api": {
        "dockerfile_content": {
          "1": "FROM python:3.11-alpine",
          "2": "WORKDIR /app",
          "3": "COPY requirements.txt .",
          "4": "RUN pip install -r requirements.txt",
          "5": "COPY . .",
          "6": "EXPOSE 8000",
          "7": "CMD [\"python\", \"app.py\"]"
        },
        "build_context": "/path/to/code",
        "startup_order": 2,
        "domain": "api.yourdomain.com",
        "servers_count": 3,
        "server_zone": "nyc3",
        "server_cpu": 2,
        "server_memory": 4096
      },
      "postgres": {}
    }
  }
}
```

**Server Configuration Defaults:**

- `servers_count`: 1 (single server)
- `server_zone`: "lon1" (London region)
- `server_cpu`: 1 (1 vCPU core - $6/month)
- `server_memory`: 1024 (1GB RAM - $6/month)

All services automatically get these cheapest defaults if not specified. Scale up as needed per service.

## 3. Deploy

```python
from deployer import Deployer

deployer = Deployer("your_project")

# Single environment
deployer.deploy(env="dev")              # build + deploy dev
deployer.deploy(env="dev", build=False) # deploy dev only (no build)

# All environments
deployer.deploy()                       # build all + deploy all
deployer.deploy(build=False)            # deploy all (no build)

# Advanced: build and push separately
deployer.build_images(env="production", push_to_registry=True)
deployer.deploy(env="production", build=False)
```

## What Happens Automatically

### Build Phase (when `build=True`)

- ✅ Builds Docker images for specified environment(s)
- ✅ Auto-detects remote servers and pushes to registry if needed
- ✅ Localhost deployments skip registry push

### Deploy Phase

- ✅ Creates `/local/project/env/` directory structure
- ✅ Auto-provisions postgres/redis/nginx with secure passwords
- ✅ Creates Docker networks and volumes
- ✅ Syncs config/secrets to servers
- ✅ Deploys containers or scheduled jobs
- ✅ **Auto-configures nginx with SSL** if domain present:
  - localhost: self-signed certs (for testing)
  - remote + .env: Let's Encrypt + Cloudflare DNS + firewall
  - remote without .env: warning, skips nginx
- ✅ Pulls logs/data back to local

## Environment Overrides

Override per environment in `environments` section:

```json
"environments": {
    "production": {
        "services": {
            "api": {
                "dockerfile_content": {
                    "4.1": "RUN useradd appuser"
                },
                "domain": "api.prod.yourdomain.com"
            }
        }
    }
}
```

## Scheduled Services

Add `"schedule"` for cron/Windows Task Scheduler jobs:

```json
"worker": {
    "dockerfile_content": {...},
    "schedule": "*/10 * * * * *"  // every 10 seconds (6-field cron)
}

"daily_report": {
    "dockerfile_content": {...},
    "schedule": "0 16 * * *"  // every day at 16:00 (5-field cron)
}
```

**Cron format:** `second minute hour day month weekday` (6 fields) or `minute hour day month weekday` (5 fields)

## Application File Paths

Your containerized apps can access these paths:

- **Configuration:** `/app/config/` (read-only)
- **Secrets:** `/app/secrets/` (read-only, secure passwords/keys)
- **Logs:** `/app/logs/` (write your application logs here)
- **Data:** `/app/data/` (persistent storage, e.g. `/app/data/uploads/`)
- **Static Files:** `/app/files/` (read-only shared files)

Example Python code:

```python
import json
from pathlib import Path

# Read config
config = json.loads(Path('/app/config/app.json').read_text())

# Read secret
db_password = Path('/app/secrets/db_password').read_text().strip()

# Write log
Path('/app/logs/app.log').write_text('Application started\n')

# Access uploaded file
user_file = Path('/app/data/uploads/user123/document.pdf')
```

These directories are automatically synced between your local machine and containers.

## That's It

One command deploys everything: containers, SSL, DNS, firewall, secrets, volumes, networks.
