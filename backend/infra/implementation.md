# Implementation Roadmap - Toggle-Based Deployment

## Phase 1: Core Foundation

### deployment_naming.py

**Changes:**

- Remove `is_proxy` parameter from all methods
- Keep methods simple - NO version parameter needed
- Add `get_container_name_pattern()` for wildcard matching

**Status:** Simple cleanup, no major changes

### deployment_port_resolver.py

**Changes:**

- Keep `generate_host_port()` as-is (no version parameter)
- Add `get_internal_port()` static method for nginx listen ports

**New method:**

```python
@staticmethod
def get_internal_port(project: str, env: str, service: str, base_port: int = 5000) -> int:
    """
    Generate deterministic internal port for nginx to listen on.

    Hash input: project_env_service_internal (no version!)
    Port range: 5000-5999
    """
```

---

## Phase 2: Docker Helpers

### execute_docker.py

**Add new methods:**

```python
@staticmethod
def find_containers_by_pattern(
    pattern: str,
    server_ip: str = 'localhost',
    user: str = "root"
) -> List[Dict[str, Any]]:
    """
    Find containers matching a name pattern.

    Returns: [{"name": "container_name", "port": 8357}, ...]

    Example:
        pattern = "new_project_uat_postgres"
        returns both "new_project_uat_postgres" and "new_project_uat_postgres_secondary"
    """
```

**Helper for toggle logic:**

```python
@staticmethod
def find_service_container(
    project: str,
    env: str,
    service: str,
    server_ip: str
) -> Optional[Dict[str, Any]]:
    """
    Find existing container for service (base or secondary).

    Returns: {"name": "...", "port": 8357} or None
    """
```

---

## Phase 3: Nginx Stream Configuration

### nginx_config_generator.py

**Add new methods:**

```python
@staticmethod
def generate_stream_config(
    project: str,
    env: str,
    service_name: str,
    backends: List[Dict[str, Any]],
    listen_port: int,
    mode: str  # NEW: "single_server" or "multi_server"
) -> str:
    """
    Generate nginx stream configuration for TCP proxying.

    Args:
        backends: Single-server: [{"container_name": "...", "port": "5432"}, ...]
                  Multi-server: [{"ip": "...", "port": 8357}, ...]
        mode: "single_server" or "multi_server"

    Example output (single-server):
    '''
    upstream new_project_uat_postgres {
        server new_project_uat_postgres:5432;
        server new_project_uat_postgres_secondary:5432;
        least_conn;
    }

    server {
        listen 5234;
        proxy_pass new_project_uat_postgres;
        proxy_connect_timeout 1s;
        proxy_timeout 10m;
    }
    '''

    Example output (multi-server):
    '''
    upstream new_project_uat_postgres {
        server 144.126.203.67:8357;
        server 134.209.183.129:18357;
        least_conn;
    }

    server {
        listen 5234;
        proxy_pass new_project_uat_postgres;
        proxy_connect_timeout 1s;
        proxy_timeout 10m;
    }
    '''
    """

    upstream_name = f"{project}_{env}_{service_name}"

    # Build upstream block based on mode
    upstream = f"upstream {upstream_name} {{\n"

    if mode == "single_server":
        for backend in backends:
            upstream += f"    server {backend['container_name']}:{backend['port']};\n"
    else:  # multi_server
        for backend in backends:
            upstream += f"    server {backend['ip']}:{backend['port']};\n"

    upstream += "    least_conn;\n"
    upstream += "}\n\n"

    # Build server block (same for both modes)
    server = f"server {{\n"
    server += f"    listen {listen_port};\n"
    server += f"    proxy_pass {upstream_name};\n"
    server += f"    proxy_connect_timeout 1s;\n"
    server += f"    proxy_timeout 10m;\n"
    server += "}\n"

    return upstream + server

@staticmethod
def setup_nginx_sidecar(
    project: str,
    env: str,
    server_ip: str,
    user: str = "root"
) -> bool:
    """
    Install nginx sidecar on a server with stream support.

    Creates:
    - /etc/nginx/nginx.conf (with stream block)
    - /etc/nginx/stream.d/ directory
    - nginx container on Docker network
    """

@staticmethod
def update_stream_config_on_server(
    server_ip: str,
    project: str,
    env: str,
    service: str,
    backends: List[Dict[str, Any]],
    internal_port: int,
    user: str = "root"
) -> bool:
    """
    Write stream config file and reload nginx.

    File: /etc/nginx/stream.d/{project}_{env}_{service}.conf
    Action: docker exec nginx nginx -s reload
    """
```

**Update nginx.conf template:**

```nginx
# Add stream block
stream {
    include /etc/nginx/stream.d/*.conf;
}

http {
    include /etc/nginx/conf.d/*.conf;
}
```

---

## Phase 4: Core Deployment Logic

### deployer.py

**Refactor \_deploy_immutable() with toggle logic:**

```python
def _deploy_immutable(self, project, env, service_name, service_config):
    """
    Zero-downtime deployment with toggle-based naming.

    High-level flow:
    1. Determine reuse strategy (dedicated vs shared servers)
    2. Calculate shortfall, create new servers if needed
    3. For each target server:
       a. Find existing container (if any)
       b. Determine toggle: base or base+10000
       c. Deploy new container alongside old
       d. Health check
       e. Record deployment state
    4. Update nginx on ALL servers in zone
    5. Stop old containers
    6. Cleanup unused servers
    """
```

**New helper methods:**

```python
def _determine_toggle(self, project, env, service, server_ip, base_port, base_name):
    """
    Determine which port/name to use based on what's currently running.

    Returns: {"port": 8357, "name": "base_name"} or
             {"port": 18357, "name": "base_name_secondary"}
    """
    existing = DockerExecuter.find_service_container(project, env, service, server_ip)

    if not existing:
        # First deployment
        return {"port": base_port, "name": base_name}

    # Toggle logic
    if existing["port"] == base_port:
        # Currently on base, use secondary
        return {"port": base_port + 10000, "name": f"{base_name}_secondary"}
    else:
        # Currently on secondary, use base
        return {"port": base_port, "name": base_name}

def _determine_backend_mode(self, deployed_servers, all_servers):
    """
    Determine if single-server (container names) or multi-server (IP+ports).

    Returns: "single_server" or "multi_server"
    """
    # Single server: all services on one machine
    if len(deployed_servers) == 1:
        # Check if any other servers exist in the zone
        if len(all_servers) == 1 and deployed_servers[0] == all_servers[0]['ip']:
            return "single_server"

    return "multi_server"

def _generate_nginx_backends(self, mode, project, env, service, deployed_servers):
    """
    Generate backend list for nginx based on deployment mode.

    Args:
        mode: "single_server" or "multi_server"
        deployed_servers: List of server IPs where service runs

    Returns:
        Single-server: [{"container_name": "...", "port": "5432"}, ...]
        Multi-server: [{"ip": "...", "port": 8357}, ...]
    """
    if mode == "single_server":
        # Use container names (Docker DNS)
        backends = []

        # Get both primary and secondary containers if they exist
        for suffix in ["", "_secondary"]:
            container_name = DeploymentNaming.get_container_name(project, env, service)
            if suffix:
                container_name = f"{container_name}{suffix}"

            # Check if container exists
            if self._container_exists(container_name, deployed_servers[0]):
                # Get container port (not host port)
                service_config = self.deployment_configurer.get_services(env)[service]
                dockerfile = service_config.get("dockerfile")
                container_ports = DeploymentPortResolver.get_container_ports(service, dockerfile)
                container_port = container_ports[0] if container_ports else "8000"

                backends.append({
                    "container_name": container_name,
                    "port": container_port  # Container port (5432, not 8357)
                })

        return backends

    else:
        # Use IP + host ports
        backends = []

        for server_ip in deployed_servers:
            # Find what's actually running on this server
            existing = DockerExecuter.find_service_container(project, env, service, server_ip)

            if existing:
                backends.append({
                    "ip": server_ip,
                    "port": existing["port"]  # Host port (8357 or 18357)
                })

        return backends

def _update_all_nginx_for_service(self, project, env, service, deployed_servers, all_servers):
    """
    Update nginx stream config on all servers in zone.
    Handles both single-server (container names) and multi-server (IP+ports).
    """
    if not self._is_tcp_service(service):
        return

    # Determine backend mode
    mode = self._determine_backend_mode(deployed_servers, all_servers)

    log(f"Nginx backend mode: {mode}")

    # Generate backends based on mode
    backends = self._generate_nginx_backends(mode, project, env, service, deployed_servers)

    if not backends:
        log(f"No backends found for {service}")
        return

    # Calculate internal port (stable)
    internal_port = DeploymentPortResolver.get_internal_port(project, env, service)

    # Update nginx on every server
    for server in all_servers:
        NginxConfigGenerator.update_stream_config_on_server(
            server['ip'], project, env, service, backends, internal_port, mode
        )

def _is_tcp_service(self, service_name):
    """Check if service needs TCP proxying."""
    tcp_services = ["postgres", "redis", "mongo", "mysql", "rabbitmq", "kafka"]
    return service_name in tcp_services

def _get_all_servers_in_zone(self, zone):
    """Get all green servers in a zone."""
    return ServerInventory.get_servers(
        deployment_status=ServerInventory.STATUS_GREEN,
        zone=zone
    )

def _should_map_host_port(self, service_name, mode):
    """
    Determine if service needs host port mapping.

    Single-server internal services: NO port mapping
    Multi-server internal services: YES port mapping
    External services: ALWAYS port mapping
    """
    if mode == "single_server" and self._is_tcp_service(service_name):
        return False  # No port mapping for internal services on single server

    return True  # Map ports for everything else
```

**Toggle deployment flow:**

```python
# For each target server
for server_ip in target_ips:
    # 1. Determine if single or multi-server
    all_zone_servers = self._get_all_servers_in_zone(zone)
    mode = self._determine_backend_mode([server_ip], all_zone_servers)

    # 2. Calculate base port/name
    base_port = DeploymentPortResolver.generate_host_port(
        project, env, service, container_port
    )
    base_name = DeploymentNaming.get_container_name(project, env, service)

    # 3. Determine toggle
    toggle = self._determine_toggle(project, env, service, server_ip, base_port, base_name)
    new_port = toggle["port"]
    new_name = toggle["name"]

    # 4. Determine if we need port mapping
    need_port_mapping = self._should_map_host_port(service_name, mode)

    # 5. Deploy with appropriate port config
    if need_port_mapping:
        ports = {str(new_port): str(container_port)}
    else:
        ports = None  # No host port mapping

    self._start_container(
        name=new_name,
        ports=ports,
        network=network_name,
        ...
    )

    # 6. Health check
    if need_port_mapping:
        health_check_url = f"http://{server_ip}:{new_port}"
    else:
        # For single-server, check container directly
        health_check_url = None  # Skip HTTP check or use docker exec

    if not self._health_check(health_check_url):
        DockerExecuter.stop_and_remove_container(new_name, server_ip)
        return False

    # 7. Stop old container
    old_name = self._get_opposite_container_name(new_name, base_name)
    if old_name:
        DockerExecuter.stop_and_remove_container(old_name, server_ip)

    # 8. Track deployed server
    deployed_servers.append(server_ip)

# 9. Update nginx everywhere (handles both modes)
self._update_all_nginx_for_service(project, env, service, deployed_servers, all_zone_servers)
```

---

## Phase 5: Server Provisioning

### do_manager.py

**Update create_droplet():**

```python
@staticmethod
def create_droplet(...):
    # Existing: create droplet, install docker, health monitor

    # NEW: Install nginx sidecar on every server
    # Note: Need to pass project/env context through provisioning
    from nginx_config_generator import NginxConfigGenerator
    NginxConfigGenerator.setup_nginx_sidecar(project, env, ip)
```

**Challenge:** Droplet provisioning doesn't know project/env context yet.

**Solution:** Install basic nginx during provisioning, configure it during first service deployment.

```python
@staticmethod
def create_droplet(...):
    # ... existing provisioning ...

    # NEW: Install basic nginx container (no configs yet)
    install_basic_nginx_container(ip)
```

```python
def install_basic_nginx_container(server_ip: str):
    """
    Install nginx container with empty stream.d directory.
    Configs added later during service deployments.
    """
    # Create directories
    CommandExecuter.run_cmd("mkdir -p /etc/nginx/stream.d", server_ip, "root")

    # Create basic nginx.conf
    nginx_conf = """
    events { worker_connections 1024; }

    stream {
        include /etc/nginx/stream.d/*.conf;
    }

    http {
        include /etc/nginx/conf.d/*.conf;
    }
    """

    # Write config
    CommandExecuter.run_cmd_with_stdin(
        "cat > /etc/nginx/nginx.conf",
        nginx_conf.encode("utf-8"),
        server_ip, "root"
    )

    # Start nginx container
    DockerExecuter.run_container(
        image="nginx:alpine",
        name="nginx",
        ports=None,  # No external ports needed
        volumes=[
            "/etc/nginx/nginx.conf:/etc/nginx/nginx.conf:ro",
            "/etc/nginx/stream.d:/etc/nginx/stream.d:ro"
        ],
        restart_policy="unless-stopped",
        server_ip=server_ip
    )
```

---

## Phase 6: Deployment State Management

### deployment_state_manager.py

**Update record_deployment():**

```python
@staticmethod
def record_deployment(
    project: str,
    env: str,
    service: str,
    servers: List[str],
    container_name: str,
    version: str,
    port: int  # NEW: Record actual port for visibility
):
```

**State format (minimal change):**

```json
{
  "myproject": {
    "uat": {
      "postgres": {
        "current": {
          "version": "latest",
          "servers": ["144.126.203.67", "134.209.183.129"],
          "container_name": "myproject_uat_postgres",
          "deployed_at": "2025-01-10T...",
          "port": 8357 // NEW: for visibility/debugging
        }
      }
    }
  }
}
```

**Note:** Port stored for debugging, not for service discovery (nginx handles that).

---

## Phase 7: Update All Call Sites

### Files that call deployment_naming.py

**Current calls (no changes needed):**

```python
# These work as-is since we removed version parameter
DeploymentNaming.get_container_name(project, env, service)
DeploymentNaming.get_image_name(docker_hub_user, project, env, service, version)
```

**Pattern to verify:**

```bash
grep -r "DeploymentNaming.get_container_name" *.py
grep -r "DeploymentNaming.get_image_name" *.py
```

Should find in:

- `deployer.py` - ✅ Works as-is
- `deployment_state_manager.py` - ✅ Works as-is
- `cron_manager.py` - ✅ Works as-is
- `nginx_config_generator.py` - ✅ Works as-is

---

## Phase 8: Testing Strategy

### Unit Tests

```python
# test_toggle_deployment.py

def test_toggle_logic():
    """Test port/name toggling"""
    # First deployment
    toggle1 = determine_toggle(None, 8357, "base")
    assert toggle1 == {"port": 8357, "name": "base"}

    # Second deployment (toggle)
    toggle2 = determine_toggle({"port": 8357}, 8357, "base")
    assert toggle2 == {"port": 18357, "name": "base_secondary"}

    # Third deployment (toggle back)
    toggle3 = determine_toggle({"port": 18357}, 8357, "base")
    assert toggle3 == {"port": 8357, "name": "base"}

def test_internal_port_stability():
    """Internal port never changes"""
    port1 = DeploymentPortResolver.get_internal_port("p", "e", "postgres")
    port2 = DeploymentPortResolver.get_internal_port("p", "e", "postgres")
    assert port1 == port2
    assert 5000 <= port1 < 6000

def test_host_port_generation():
    """Host port deterministic"""
    port1 = DeploymentPortResolver.generate_host_port("p", "e", "s", "5432")
    port2 = DeploymentPortResolver.generate_host_port("p", "e", "s", "5432")
    assert port1 == port2
    assert 8000 <= port1 < 9000
```

### Integration Tests

1. **Initial deployment:**
   - Deploy postgres to 2 servers
   - Verify containers: `new_project_uat_postgres` on port `8357`
   - Verify nginx configs on all servers point to `8357`
2. **Update deployment (toggle):**
   - Deploy postgres update
   - Verify new containers: `new_project_uat_postgres_secondary` on `18357`
   - Verify nginx updated to point to `18357`
   - Verify old containers stopped
3. **Third deployment (toggle back):**
   - Deploy again
   - Verify containers: `new_project_uat_postgres` on `8357`
   - Verify nginx updated to `8357`
4. **Service discovery:**
   - Deploy API that depends on postgres
   - Verify API can connect to `localhost:5234`
   - Verify postgres receives connections

---

## Phase 9: Migration from Existing System

### Strategy

1. **Deploy new code** with toggle logic
2. **Existing deployments** continue working (same naming)
3. **Next deployment** triggers toggle mechanism
4. **Gradually migrate** services one by one

### Backward Compatibility

```python
# deployer.py - handle both old and new formats
def _start_service(self, ...):
    # Check if we're in toggle mode (new) or old mode
    if use_toggle_deployment:
        toggle = self._determine_toggle(...)
        port = toggle["port"]
        name = toggle["name"]
    else:
        # Legacy behavior
        port = base_port
        name = base_name
```

**Toggle by feature flag:**

```json
{
  "project": {
    "use_toggle_deployment": true // Enable new system
  }
}
```

---

## Phase 10: Nginx Config Examples

### Stream Config for Postgres

```nginx
# /etc/nginx/stream.d/new_project_uat_postgres.conf

upstream new_project_uat_postgres {
    server 144.126.203.67:8357;
    server 134.209.183.129:18357;  # Mixed toggle states OK!
    least_conn;
}

server {
    listen 5234;
    proxy_pass new_project_uat_postgres;
    proxy_connect_timeout 1s;
    proxy_timeout 10m;
    proxy_next_upstream_timeout 5s;
    proxy_next_upstream_tries 2;
}
```

### Stream Config for Redis

```nginx
# /etc/nginx/stream.d/new_project_uat_redis.conf

upstream new_project_uat_redis {
    server 144.126.203.67:8612;
}

server {
    listen 5789;
    proxy_pass new_project_uat_redis;
    proxy_connect_timeout 1s;
    proxy_timeout 1h;
}
```

---

## Estimated Effort

### Critical Path

1. **Phase 2:** Docker helpers - 1 hour
2. **Phase 3:** Nginx stream config - 3 hours
3. **Phase 4:** Core deployment refactor - 4 hours
4. **Phase 5:** Server provisioning - 1 hour
5. **Phase 8:** Testing - 2 hours

**Total:** ~11 hours of focused development

### Nice-to-Have

- Phase 6: State management updates - 1 hour
- Phase 9: Migration tooling - 1 hour
- Documentation - 2 hours

---

## Risk Mitigation

### Risks

1. **Toggle logic bug** → Deploy to wrong port/name
2. **Nginx reload fails** → Old config still active
3. **Port collision** → Two services try same port
4. **Nginx sidecar missing** → Apps can't connect

### Mitigations

1. Extensive unit tests for toggle logic
2. Nginx reload is graceful, test in dev first
3. Hash space is large (1000 ports per range)
4. Health check nginx container during deployment

---

## Success Criteria

### Must Have

- ✅ Zero downtime deployments work reliably
- ✅ Apps connect via localhost successfully
- ✅ Toggle alternates correctly each deployment
- ✅ Old containers stopped after new ones healthy

### Should Have

- ✅ Nginx configs update on all servers < 30 seconds
- ✅ Latency overhead < 2ms per request
- ✅ Clear visibility of toggle state (`docker ps`)

### Nice to Have

- ✅ Automatic nginx sidecar provisioning
- ✅ Health monitoring of nginx sidecars
- ✅ Metrics on nginx routing performance

---

## Deployment Checklist

### Before First Deployment

- [ ] Phase 1-3 code complete
- [ ] Unit tests passing
- [ ] Dev environment tested
- [ ] Documentation updated

### First Deployment

- [ ] Deploy to single dev server
- [ ] Verify nginx sidecar works
- [ ] Verify toggle logic works
- [ ] Deploy second time, verify toggle back

### Production Rollout

- [ ] Deploy to staging zone
- [ ] Monitor for 24 hours
- [ ] Gradually roll to production zones
- [ ] Monitor latency and errors

---

## Next Steps

1. ✅ PLAN.md and IMPLEMENTATION.md reviewed
2. Start with Phase 2 (Docker helpers)
3. Implement Phase 3 (Nginx stream config)
4. Refactor Phase 4 (Core deployment with toggle)
5. Test thoroughly in dev
6. Deploy to staging
7. Production rollout

**Simpler than version-based approach - ~30% less code to write!**# Implementation Roadmap

## Phase 1: Core Foundation (COMPLETED)

### ✅ deployment_naming.py

**Changes:**

- Remove `is_proxy` parameter from all methods
- Add `version` parameter to `get_container_name()`
- Add `_sanitize_version()` helper
- Add `get_container_name_pattern()` for wildcard matching

**Status:** Artifact created, ready to replace existing file

### ✅ deployment_port_resolver.py

**Changes:**

- Add `version` parameter to `generate_host_port()`
- Add `get_internal_port()` static method for nginx listen ports
- Remove `get_actual_port_from_state()` (not needed)

**Status:** Artifact created, ready to replace existing file

---

## Phase 2: Docker Helpers

### execute_docker.py

**Add new methods:**

```python
@staticmethod
def find_containers_by_pattern(
    pattern: str,
    server_ip: str = 'localhost',
    user: str = "root"
) -> List[Dict[str, str]]:
    """
    Find containers matching a name pattern.
    Returns: [{"name": "...", "port": "..."}, ...]
    """
```

**Update existing method:**

```python
@staticmethod
def run_container(..., labels: Optional[Dict[str, str]] = None):
    """Add labels parameter for container metadata"""
```

---

## Phase 3: Deployment State Management

### deployment_state_manager.py

**Update record_deployment():**

```python
@staticmethod
def record_deployment(
    project: str,
    env: str,
    service: str,
    servers: List[str],
    container_name: str,
    version: str,  # Now critical, not just metadata
    port: int      # NEW: actual host port for this version
):
```

**State format change:**

```json
{
  "myproject": {
    "uat": {
      "postgres": {
        "current": {
          "version": "15.2",
          "servers": ["144.126.203.67", "134.209.183.129"],
          "container_name": "new_project_uat_postgres_v15_2",
          "deployed_at": "2025-01-10T...",
          "port": 8412 // NEW: actual host port
        }
      }
    }
  }
}
```

---

## Phase 4: Nginx Stream Configuration

### nginx_config_generator.py

**Add new methods:**

```python
@staticmethod
def generate_stream_config(
    project: str,
    env: str,
    service_name: str,
    backends: List[Dict[str, Any]],  # [{"ip": "...", "port": 8412}, ...]
    listen_port: int
) -> str:
    """
    Generate nginx stream configuration for TCP proxying.

    Returns:
    '''
    upstream new_project_uat_postgres {
        server 144.126.203.67:8412;
        server 134.209.183.129:8412;
        least_conn;
    }

    server {
        listen 5234;
        proxy_pass new_project_uat_postgres;
        proxy_connect_timeout 1s;
        proxy_timeout 10m;
    }
    '''
    """

@staticmethod
def setup_nginx_sidecar(
    project: str,
    env: str,
    server_ip: str,
    user: str = "root"
) -> bool:
    """
    Install nginx sidecar on a server with stream support.

    Creates:
    - /etc/nginx/nginx.conf (with stream block)
    - /etc/nginx/stream.d/ directory
    - nginx container on Docker network
    """

@staticmethod
def update_stream_config_on_server(
    server_ip: str,
    project: str,
    env: str,
    service: str,
    backends: List[Dict[str, Any]],
    internal_port: int,
    user: str = "root"
) -> bool:
    """
    Write stream config file and reload nginx.

    File: /etc/nginx/stream.d/{project}_{env}_{service}.conf
    """
```

**Update nginx.conf template:**

```nginx
# Add stream block
stream {
    include /etc/nginx/stream.d/*.conf;
}

http {
    include /etc/nginx/conf.d/*.conf;
}
```

---

## Phase 5: Server Provisioning

### do_manager.py

**Update create_droplet():**

```python
@staticmethod
def create_droplet(...):
    # Existing: create droplet, install docker, health monitor

    # NEW: Install nginx sidecar on every server
    from nginx_config_generator import NginxConfigGenerator
    NginxConfigGenerator.setup_nginx_sidecar(project, env, ip)
```

**Note:** Need to pass project/env context through provisioning chain

---

## Phase 6: Core Deployment Logic

### deployer.py

**Major refactor of \_deploy_immutable():**

```python
def _deploy_immutable(self, project, env, service_name, service_config):
    """
    Zero-downtime deployment with version-based naming.

    High-level flow:
    1. Determine reuse strategy (dedicated vs shared servers)
    2. Calculate shortfall, create new servers if needed
    3. For each target server:
       a. Generate version-based names/ports
       b. Deploy new container alongside old
       c. Health check
       d. Update deployment state
    4. Update nginx on ALL servers in zone
    5. Stop old version containers
    6. Cleanup unused servers
    """
```

**New helper methods:**

```python
def _find_existing_service_container(self, project, env, service, server_ip):
    """Find existing container for this service (any version)."""

def _calculate_service_ports(self, project, env, service, version):
    """Calculate both host_port and internal_port for a service/version."""

def _update_all_nginx_for_service(self, project, env, service, backends, all_servers):
    """Update nginx stream config on all servers in zone."""

def _is_tcp_service(self, service_name):
    """Check if service needs TCP proxying (postgres, redis, etc.)."""

def _get_all_servers_in_zone(self, zone):
    """Get all green servers in a zone (for nginx updates)."""
```

**Version handling:**

```python
# Get version from config (source of truth)
version = service_config.get("version") or self.deployment_configurer.get_version()

# Generate versioned names
container_name = DeploymentNaming.get_container_name(project, env, service_name, version)
host_port = DeploymentPortResolver.generate_host_port(project, env, service_name, container_port, version)
internal_port = DeploymentPortResolver.get_internal_port(project, env, service_name)
```

---

## Phase 7: Update All Call Sites

### Files that call deployment_naming.py methods

**Need version parameter added:**

- `deployer.py` - multiple call sites
- `deployment_state_manager.py` - container name generation
- `cron_manager.py` - scheduled service names
- `scheduler_manager.py` - scheduled service names
- `nginx_config_generator.py` - container name references

**Pattern to find:**

```bash
grep -r "DeploymentNaming.get_container_name" *.py
grep -r "DeploymentNaming.get_image_name" *.py
```

### Files that call deployment_port_resolver.py

**Need version parameter added:**

- `deployer.py` - port generation
- Any service discovery helpers

---

## Phase 8: Testing Strategy

### Unit Tests

```python
# test_versioned_naming.py
def test_container_name_includes_version():
    name = DeploymentNaming.get_container_name("proj", "dev", "api", "1.0.1")
    assert name == "proj_dev_api_v1_0_1"

def test_different_versions_get_different_ports():
    port1 = DeploymentPortResolver.generate_host_port("p", "e", "s", "5432", "15.2")
    port2 = DeploymentPortResolver.generate_host_port("p", "e", "s", "5432", "15.3")
    assert port1 != port2

def test_internal_port_stable_across_versions():
    port1 = DeploymentPortResolver.get_internal_port("p", "e", "s")
    port2 = DeploymentPortResolver.get_internal_port("p", "e", "s")
    assert port1 == port2  # Same regardless of version
```

### Integration Tests

1. Deploy postgres v15.2 to 2 servers
2. Verify nginx configs created on all servers in zone
3. Deploy API, verify it can connect via localhost:INTERNAL_PORT
4. Update postgres to v15.3
5. Verify nginx configs updated
6. Verify API still connected (zero downtime)
7. Verify old containers stopped

---

## Phase 9: Migration from Existing System

### Backward Compatibility Period

```python
# deployment_naming.py - temporary dual support
@staticmethod
def get_container_name(project, env, service, version=None):
    """Support both old (no version) and new (with version) callers."""
    if version:
        # New versioned format
        return f"{project}_{env}_{service}_v{sanitize(version)}"
    else:
        # Old format for backward compatibility
        return f"{project}_{env}_{service}"
```

### Gradual Rollout

1. Deploy new code (with backward compatibility)
2. Update config files to include versions
3. Deploy services one by one with new system
4. Monitor for issues
5. Remove backward compatibility code after full migration

---

## Phase 10: Documentation

### User Guide

- How to configure service versions
- How apps discover services (localhost:PORT)
- How to rollback deployments
- Troubleshooting nginx routing

### Developer Guide

- Port architecture explanation
- How version hashing works
- Adding new TCP services
- Nginx config generation

### Operations Guide

- Monitoring nginx sidecars
- Debugging connection issues
- Manual nginx config updates
- Health check procedures

---

## Estimated Effort

### Critical Path

1. **Phase 2-3:** Docker helpers + State management - 2 hours
2. **Phase 4:** Nginx stream config - 3 hours
3. **Phase 6:** Core deployment refactor - 5 hours
4. **Phase 7:** Update call sites - 3 hours
5. **Phase 8:** Testing - 2 hours

**Total:** ~15 hours of focused development

### Nice-to-Have

- Phase 5: Auto-provision nginx - 1 hour
- Phase 9: Migration tooling - 2 hours
- Phase 10: Documentation - 2 hours

---

## Risk Mitigation

### Risks

1. **Nginx reload fails** → Old config still active, apps work
2. **Port collisions** → Extremely unlikely (1/1000 chance per service)
3. **Version collision** → Add timestamp suffix if redeploying same version
4. **Nginx sidecar crashes** → Apps can't connect to backends

### Mitigations

1. Test nginx reload in dev environment first
2. Hash space is large (1000 ports per range)
3. Add timestamp to version if needed: `15.2_1736524800`
4. Monitor nginx containers, auto-restart on failure

---

## Success Criteria

### Must Have

- ✅ Zero downtime deployments work reliably
- ✅ Apps connect via localhost successfully
- ✅ Rollback works without code changes
- ✅ Version changes trigger new ports automatically

### Should Have

- ✅ Nginx configs update on all servers < 30 seconds
- ✅ Latency overhead < 2ms per request
- ✅ Clear visibility of running versions (`docker ps`)

### Nice to Have

- ✅ Automatic nginx sidecar provisioning
- ✅ Health monitoring of nginx sidecars
- ✅ Metrics on nginx routing performance

---

## Next Steps

1. Review PLAN.md and IMPLEMENTATION.md
2. Confirm architecture decisions
3. Start with Phase 2 (Docker helpers)
4. Implement incrementally with testing
5. Deploy to dev environment first
6. Validate before production rollout
