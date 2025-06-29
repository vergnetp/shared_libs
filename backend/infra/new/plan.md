# Centralized IP Management Plan

## Overview
Unified deployment system for managing allowed IPs across PostgreSQL, Redis, and OpenSearch using the existing secrets deployment pipeline.

## Current State
- Secrets stored on deployment server (dev machine)
- `secrets.json` deployed to containers via volume mounts
- Manual IP management in service config files

## Target Architecture

### 1. Service Registry File
**File**: `services.json` (alongside `secrets.json`)
```json
{
  "services": {
    "web_servers": [
      {"name": "web1", "ip": "10.0.1.50", "port": 8080}
    ],
    "postgres_allowed_ips": ["10.0.1.50", "10.0.1.51", "10.0.1.60"],
    "redis_bind_ips": ["127.0.0.1", "10.0.1.100"],
    "opensearch_network_hosts": ["127.0.0.1", "10.0.1.100"]
  },
  "updated": "2025-06-24T08:30:00Z"
}
```

### 2. Unified Deployment Pipeline
```
Dev Machine → {secrets.json, services.json} → Deploy to all containers → Mount both files
```

### 3. Container Auto-Configuration
Each container runs periodic script (every 10 minutes):
1. Read `services.json`
2. Generate appropriate config file:
   - **PostgreSQL**: `pg_hba.conf` with allowed IPs
   - **Redis**: `redis.conf` with bind IPs
   - **OpenSearch**: `opensearch.yml` with network hosts
3. Reload service configuration (zero downtime)

## Implementation Steps

### Phase 1: Extend Secrets System
1. Add `services.json` to existing secrets deployment
2. Mount both files into containers
3. Update container generator to include config scripts

### Phase 2: PostgreSQL IP Management
1. Update PostgreSQL Dockerfile with IP discovery script
2. Script reads `services.json` → generates `pg_hba.conf`
3. Test with backup worker connectivity

### Phase 3: Redis & OpenSearch
1. Extend same pattern to Redis and OpenSearch containers
2. Each service reads appropriate section from `services.json`
3. Generate service-specific config files

### Phase 4: Operational Workflow
**Adding new server:**
1. Edit `services.json` on deployment server
2. Run unified deployment script
3. All containers automatically detect and apply changes
4. Zero downtime configuration updates

## Benefits
- **Centralized IP management** across entire stack
- **Version control** via Git
- **Atomic updates** (secrets + services together)
- **Zero downtime** configuration changes
- **Leverages existing** secrets infrastructure
- **No additional dependencies** (no Consul/service discovery complexity)

## Security
- Same access controls as secrets
- Network-level IP restrictions
- Service-specific authentication (passwords, keys)
- Audit trail via deployment logs