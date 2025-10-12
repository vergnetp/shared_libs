# Toggle-Based Deployment with Nginx Service Mesh

## Overview

Implement **zero-downtime deployments** using **port/name toggling** with **nginx sidecar service mesh** for internal service discovery.

---

## Core Concepts

### 1. Port Toggle Strategy

**Every service has two identities that alternate each deployment:**

```
Deployment 1:
  Container: new_project_uat_postgres
  Port: 8357 (base)

Deployment 2 (update):
  Container: new_project_uat_postgres_secondary
  Port: 18357 (base + 10000)

Deployment 3 (update):
  Container: new_project_uat_postgres
  Port: 8357 (back to base)
```

**Toggle Logic:**

- Check what's currently running
- If using base → use base+10000 with \_secondary suffix
- If using base+10000 → use base with no suffix
- Ping-pong between primary and secondary each deployment

**Benefits:**

- ✅ Simple: No version tracking needed
- ✅ Deterministic: Always know which port/name to use
- ✅ Zero downtime: Old runs while new starts
- ✅ No port conflicts: Offset guarantees separation

### 2. Three-Tier Port Architecture

**Container Port (internal to container):**

- Fixed by service: postgres=5432, redis=6379
- Never exposed to host

**Host Port (where container actually listens on server):**

- Base: `hash(project_env_service_containerport) % 1000 + 8000`
- Secondary: `base + 10000`
- Range: 8000-8999 (base), 18000-18999 (secondary)
- Example: postgres base → 8357, secondary → 18357

**Internal Port (what apps connect to via nginx):**

- Generated: `hash(project_env_service_internal) % 1000 + 5000`
- Range: 5000-5999
- Stable across deployments (never toggles)
- Example: new_project/uat/postgres always → 5234

### 3. Nginx Sidecar Service Mesh

**Architecture:**

```
App Container (any server)
  ↓ connects to localhost:5234
  ↓
Nginx Sidecar (same server)
  ↓ routes to backends
  ↓
Postgres Containers (backend servers)
  listening on 8357 or 18357 (toggles)
```

**Key Design Decisions:**

- ✅ Nginx deployed on EVERY server automatically
- ✅ Apps always connect to `localhost:INTERNAL_PORT`
- ✅ Nginx configs updated on ALL servers when backend changes
- ✅ Internal port never changes (stable interface for apps)

### 4. Single Server vs Multi-Server Nginx Routing

**Docker networking requires different approaches:**

#### Single Server Deployment

When all containers are on the same server:

```
Server (localhost):
├─ nginx container (network: new_project_uat_network)
├─ postgres container (network: new_project_uat_network)
└─ api container (network: new_project_uat_network)
```

**Nginx config uses container names (Docker DNS):**

```nginx
upstream new_project_uat_postgres {
    server new_project_uat_postgres:5432;           # Container name + container port
    server new_project_uat_postgres_secondary:5432; # Can have both during toggle
}

server {
    listen 5234;
    proxy_pass new_project_uat_postgres;
}
```

**Benefits:**

- ✅ No host port mapping needed (containers talk directly)
- ✅ Docker DNS resolves container names automatically
- ✅ Simpler, more efficient (no port mapping overhead)
- ✅ Container IPs can change freely

**Container deployment (no ports):**

```python
DockerExecuter.run_container(
    name="new_project_uat_postgres",
    ports=None,  # No host port mapping!
    network="new_project_uat_network"
)
```

#### Multi-Server Deployment

When containers are on different servers:

```
Server 1 (API):
└─ nginx container → routes to Server 2

Server 2 (Postgres):
└─ postgres container (host port 8357 mapped)
```

**Nginx config uses IP + host ports:**

```nginx
upstream new_project_uat_postgres {
    server 144.126.203.67:8357;   # Server IP + host port
    server 134.209.183.129:18357; # Different server, different port
}

server {
    listen 5234;
    proxy_pass new_project_uat_postgres;
}
```

**Why:**

- ❌ Container names don't work across servers (different Docker networks)
- ✅ Must use server IP + mapped host port
- ✅ Host port mapping required for cross-server communication

**Container deployment (with ports):**

```python
DockerExecuter.run_container(
    name="new_project_uat_postgres",
    ports={"8357": "5432"},  # Map host:container
    network="new_project_uat_network"
)
```

#### Detection Logic

```python
def determine_nginx_backend_mode(deployed_servers, all_servers):
    """
    Single-server: All services on one server
    Multi-server: Services distributed across servers
    """
    if len(deployed_servers) == 1 and deployed_servers[0] in all_servers:
        return "single_server"  # Use container names
    else:
        return "multi_server"   # Use IP + ports
```

#### Why This Matters

- **Single server:** More efficient (no port mapping)
- **Multi-server:** Required for cross-server communication
- **Toggle works in both:** Container names toggle, or ports toggle
- **Apps don't care:** Always `localhost:INTERNAL_PORT`

---

## Deployment Flow Example

### Initial Deployment: Postgres

1. **Deploy to 2 servers:**

   - Server A: Container `new_project_uat_postgres` on port `8357`
   - Server B: Container `new_project_uat_postgres` on port `8357`

2. **Update nginx on ALL servers in zone:**

   ```nginx
   # /etc/nginx/stream.d/new_project_uat_postgres.conf
   upstream new_project_uat_postgres {
       server 144.126.203.67:8357;
       server 134.209.183.129:8357;
   }

   server {
       listen 5234;  # Internal port (hashed from project/env/service)
       proxy_pass new_project_uat_postgres;
   }
   ```

3. **Apps connect:** `DATABASE_URL=postgresql://user:pass@localhost:5234/db`

### Update Postgres (Second Deployment)

1. **Detect existing containers:**

   - Server A: `new_project_uat_postgres` on `8357` (running)
   - Server B: `new_project_uat_postgres` on `8357` (running)

2. **Deploy new version with toggle:**

   - Server A: NEW `new_project_uat_postgres_secondary` on port `18357`
   - Server B: NEW `new_project_uat_postgres_secondary` on port `18357`
   - OLD containers still running on `8357`

3. **Health check new containers**

4. **Update nginx on ALL servers:**

   ```nginx
   upstream new_project_uat_postgres {
       server 144.126.203.67:18357;  # ← Toggled ports
       server 134.209.183.129:18357;
   }

   server {
       listen 5234;  # Same internal port!
       proxy_pass new_project_uat_postgres;
   }
   ```

5. **Reload nginx:** `docker exec nginx nginx -s reload` (graceful, ~1ms)

6. **Stop old containers:** Remove containers on `8357`

7. **Apps never noticed** - still connecting to `localhost:5234`

### Third Deployment (Toggle Back)

1. **Detect existing:**

   - `new_project_uat_postgres_secondary` on `18357` (running)

2. **Deploy with toggle:**
   - NEW `new_project_uat_postgres` on `8357` (back to base!)
3. **Update nginx to point to `8357`**

4. **Stop secondary containers on `18357`**

**Pattern: Ping-pong between primary/secondary each deployment**

---

## Service Discovery

### In App Code

```python
# Method 1: Calculate internal port from project/env/service
from deployment_port_resolver import DeploymentPortResolver

db_port = DeploymentPortResolver.get_internal_port(
    "new_project", "uat", "postgres"
)

DATABASE_URL = f"postgresql://user:pass@localhost:{db_port}/db"
```

```python
# Method 2: Via environment variables (set during deployment)
import os

DB_HOST = os.getenv("DB_HOST")  # "localhost"
DB_PORT = os.getenv("DB_PORT")  # "5234" (set by deployer)
DATABASE_URL = f"postgresql://user:pass@{DB_HOST}:{DB_PORT}/db"
```

---

## Configuration Example

```json
{
  "project": {
    "name": "new_project",
    "version": "1.0.0",
    "services": {
      "postgres": {
        "image": "postgres:15",
        "servers_count": 2,
        "server_zone": "lon1"
      },
      "redis": {
        "image": "redis:7-alpine",
        "servers_count": 1,
        "server_zone": "lon1"
      },
      "api": {
        "dockerfile": "Dockerfile.api",
        "servers_count": 3,
        "server_zone": "lon1"
      }
    }
  }
}
```

**Clean - no version per service needed!**

---

## Benefits Summary

### Zero Downtime

- New deployment runs alongside old
- Traffic switched at nginx level (graceful reload)
- Old stopped only after new is healthy

### Clean Service Discovery

- Apps always: `localhost:INTERNAL_PORT`
- No hardcoded IPs or dynamic lookups
- Internal port never changes

### Simple Implementation

- No version tracking in names/ports
- Toggle logic: ~20 lines of code
- No complex state management

### Immutable Infrastructure

- Each deployment creates fresh containers
- Never modify running containers
- Easy to see what's running: `docker ps`

---

## Toggle Logic Details

```python
# Determine container name and port
base_port = DeploymentPortResolver.generate_host_port(
    project, env, service, container_port
)
base_name = DeploymentNaming.get_container_name(project, env, service)

# Find existing container (if any)
existing = find_existing_service_container(server_ip, base_name)

if existing:
    # Toggle: use opposite of current
    if existing.port == base_port:
        new_port = base_port + 10000
        new_name = f"{base_name}_secondary"
    else:
        new_port = base_port
        new_name = base_name
else:
    # First deployment: use base
    new_port = base_port
    new_name = base_name

# Deploy with new_name and new_port
```

---

## Edge Cases Handled

### Multiple Projects on Same Server

```
new_project/uat/postgres → localhost:5234
other_project/prod/postgres → localhost:5678
```

Different internal ports (hashed from project/env/service).

### Same Service, Different Envs

```
new_project/uat/postgres → localhost:5234
new_project/prod/postgres → localhost:5789
```

Different internal ports (env in hash).

### Rapid Deployments

```
Deploy 1: base (8357)
Deploy 2: secondary (18357)
Deploy 3: base (8357)
```

Toggle automatically alternates.

### Failed Deployment

```
Deploy to secondary (18357)
Health check fails
→ Stop and remove secondary
→ Base (8357) still running (zero downtime maintained)
```

---

## Latency Impact

### Direct Connection (without nginx)

App → Postgres (localhost): **~0.1ms**

### Via Nginx Sidecar

App → Nginx (localhost): **~0.1ms**
Nginx → Postgres (private network): **~0.5-1ms**
**Total: ~0.6-1.1ms overhead**

### Acceptable?

- ✅ Yes for most apps (queries typically 10-100ms+)
- ✅ Clean architecture worth the cost
- ✅ DigitalOcean private network is free and fast

---

## Security Considerations

### No External Port Exposure

- Backend services (postgres, redis) never exposed to internet
- Only accessible via nginx on localhost
- Host ports (8000-8999, 18000-18999) only on private network

### Secrets Management

- Database credentials in `/local/project/env/secrets/`
- Mounted read-only into containers
- Not in environment variables

---

## Failure Scenarios

### Nginx Fails to Reload

- Old nginx config still active
- Apps continue working with old backends
- Manual intervention to fix config

### Health Check Fails

- New containers stopped immediately
- Old containers keep running
- Zero downtime maintained

### Partial Deployment (1 of 2 servers fails)

- Fail-fast: Stop deployment on first failure
- Rollback new containers
- Old containers still running
- Can retry deployment

---

## Future Enhancements

### Multi-Zone Deployments

- Nginx in each zone routes to local backends first
- Cross-zone routing for failover
- Geo-routing via Cloudflare Load Balancer

### Dynamic Scaling

- Add server → nginx auto-configured
- Remove server → nginx updated automatically
- No manual intervention

### Advanced Health Checks

- Nginx active health checks
- Automatic backend removal on failure
- Circuit breaker pattern

---

## Decision Log

**Why toggle instead of version-based?**

- Simpler implementation (~80% less complexity)
- No version tracking needed
- Nginx abstracts backend details anyway
- Apps don't care about backend identity

**Why +10000 offset?**

- Base ports: 8000-8999
- Secondary: 18000-18999
- No collision possible between ranges

**Why nginx on every server vs centralized?**

- Lower latency (~0.6ms vs ~2-4ms)
- No single point of failure
- Simple uniform architecture

**Why update ALL servers' nginx?**

- Uniform state across zone
- Any server can talk to any service
- Worth ~10 seconds overhead per deployment

**Why hash-based ports vs config?**

- Zero configuration needed
- Deterministic and reproducible
- Impossible to have port conflicts
