"""
Nginx Service - High-level nginx operations for deployments.

Combines NginxManager (config generation) with NodeAgentClient (applying configs).
All nginx logic should go through this service - API routes should be thin wrappers.
"""

from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass

from .nginx_manager import NginxManager, BackendMode
from .ports import DeploymentPortResolver


LogCallback = Callable[[str], None]


# Services that use TCP stream (not HTTP)
STATEFUL_SERVICES = {
    "postgres", "postgresql", "mysql", "mariadb", 
    "redis", "mongo", "mongodb", "opensearch", "elasticsearch"
}


@dataclass
class NginxResult:
    """Result from nginx operations."""
    success: bool
    data: Dict[str, Any] = None
    error: Optional[str] = None


class NginxService:
    """
    High-level nginx operations for deployments.
    
    Usage:
        from infra.node_agent import NodeAgentClient
        
        client = NodeAgentClient(ip, api_key)
        nginx = NginxService(client)
        
        # Ensure nginx is running
        await nginx.ensure_running()
        
        # Set up sidecar config for a service
        result = await nginx.setup_service_sidecar(
            user_id="u1",
            project="myapp",
            environment="prod",
            service="postgres",
            container_name="myapp_prod_postgres",
            container_port=5432,
        )
    """
    
    def __init__(self, client, log: LogCallback = None):
        """
        Args:
            client: NodeAgentClient instance
            log: Optional logging callback
        """
        self.client = client
        self.log = log or (lambda msg: None)
        self.manager = NginxManager()
    
    # =========================================================================
    # Core Operations
    # =========================================================================
    
    async def ensure_running(self) -> NginxResult:
        """
        Ensure nginx container is running on the server.
        
        Creates directories and starts nginx if not running.
        """
        self.log("🔧 Setting up nginx on server...")
        
        result = await self.client.ensure_nginx_running()
        
        if result.success:
            status = result.data.get("status", "started")
            if status == "already_running":
                self.log("✅ Nginx already running")
            else:
                self.log("✅ Nginx started on ports 80/443")
            return NginxResult(success=True, data=result.data)
        else:
            self.log(f"⚠️ Nginx setup failed: {result.error}")
            return NginxResult(success=False, error=result.error)
    
    async def reload(self) -> NginxResult:
        """Reload nginx configuration."""
        result = await self.client.reload_nginx()
        if result.success:
            return NginxResult(success=True)
        return NginxResult(success=False, error=result.error)
    
    async def test_config(self) -> NginxResult:
        """Test nginx configuration for errors."""
        result = await self.client.test_nginx_config()
        if result.success:
            return NginxResult(success=True)
        return NginxResult(success=False, error=result.error)
    
    # =========================================================================
    # Sidecar Configuration (Service Discovery)
    # =========================================================================
    
    async def setup_service_sidecar(
        self,
        user_id: str,
        project: str,
        environment: str,
        service: str,
        container_name: str,
        container_port: int,
        is_stateful: bool = False,
        backends: List[Dict[str, Any]] = None,
        mode: str = "single_server",
    ) -> NginxResult:
        """
        Set up nginx sidecar config for a service.
        
        SIDECAR PATTERN: Every server runs nginx that provides service discovery.
        Apps connect to nginx:INTERNAL_PORT - nginx routes to actual backends.
        
        Args:
            user_id: User ID
            project: Project name  
            environment: Environment (prod/staging/dev)
            service: Service name
            container_name: Primary container name (for single_server mode)
            container_port: Container's internal port (5432, 6379, etc.)
            is_stateful: Is this a stateful service (postgres, redis)
            backends: List of backends for multi_server mode:
                      [{"ip": "10.0.0.1", "port": 8357}, ...]
            mode: "single_server" (Docker DNS) or "multi_server" (IPs)
        
        Returns:
            NginxResult with internal_port, config_name, mode
        """
        service_lower = service.lower()
        
        # Determine if TCP (stream) or HTTP
        use_stream = service_lower in STATEFUL_SERVICES or is_stateful
        
        # Generate stable internal port
        internal_port = DeploymentPortResolver.get_internal_port(
            user_id, project, environment, service
        )
        config_name = f"{project}_{environment}_{service}"
        
        # Build backends block
        # Note: nginx runs with --network host, so use 127.0.0.1 to reach containers
        if mode == "multi_server" and backends:
            backend_lines = []
            for b in backends:
                # Use 127.0.0.1 since nginx runs with host network
                # Each server's nginx only routes to its own local containers
                backend_lines.append(
                    f"    server 127.0.0.1:{b['port']} max_fails=3 fail_timeout=30s;"
                )
            backends_block = "\n".join(backend_lines)
        else:
            # Single server mode: use localhost to reach containers on same host
            backends_block = f"    server 127.0.0.1:{container_port} max_fails=3 fail_timeout=30s;"
        
        if use_stream:
            config = self._generate_stream_sidecar(config_name, internal_port, backends_block, mode)
            config_path = f"/local/nginx/stream.d/{config_name}.conf"
            config_type = "stream"
        else:
            config = self._generate_http_sidecar(config_name, internal_port, backends_block, mode)
            config_path = f"/local/nginx/conf.d/{config_name}_internal.conf"
            config_type = "http"
        
        try:
            await self.client.write_file(config_path, config)
            await self.client.reload_nginx()
            
            if mode == "multi_server" and backends:
                self.log(f"🔀 Nginx sidecar ({config_type}): nginx:{internal_port} → {len(backends)} backends")
            else:
                self.log(f"🔀 Nginx sidecar ({config_type}): nginx:{internal_port} → {container_name}:{container_port}")
            
            return NginxResult(
                success=True,
                data={
                    "internal_port": internal_port,
                    "config_name": config_name,
                    "mode": mode,
                    "config_path": config_path,
                }
            )
            
        except Exception as e:
            self.log(f"⚠️ Nginx {config_type} config failed: {e}")
            return NginxResult(success=False, error=str(e))
    
    async def update_sidecar_backends(
        self,
        user_id: str,
        project: str,
        environment: str,
        service: str,
        backends: List[Dict[str, Any]],
    ) -> NginxResult:
        """
        Update nginx sidecar config with new backends.
        
        Use when:
        - Adding/removing servers
        - Zero-downtime deploy (add new container, update config, remove old)
        
        Args:
            user_id: User ID
            project: Project name
            environment: Environment
            service: Service name
            backends: New list of backends [{"ip": "...", "port": ...}, ...]
        """
        service_lower = service.lower()
        use_stream = service_lower in STATEFUL_SERVICES
        
        internal_port = DeploymentPortResolver.get_internal_port(
            user_id, project, environment, service
        )
        config_name = f"{project}_{environment}_{service}"
        
        backend_lines = []
        for b in backends:
            # Use 127.0.0.1 since nginx runs with host network
            backend_lines.append(
                f"    server 127.0.0.1:{b['port']} max_fails=3 fail_timeout=30s;"
            )
        backends_block = "\n".join(backend_lines)
        
        if use_stream:
            config = self._generate_stream_sidecar(config_name, internal_port, backends_block, "multi_server")
            config_path = f"/local/nginx/stream.d/{config_name}.conf"
        else:
            config = self._generate_http_sidecar(config_name, internal_port, backends_block, "multi_server")
            config_path = f"/local/nginx/conf.d/{config_name}_internal.conf"
        
        try:
            await self.client.write_file(config_path, config)
            await self.client.reload_nginx()
            
            self.log(f"🔄 Updated sidecar: {config_name} → {len(backends)} backends")
            
            return NginxResult(
                success=True,
                data={"internal_port": internal_port, "backends": len(backends)}
            )
            
        except Exception as e:
            self.log(f"⚠️ Failed to update sidecar: {e}")
            return NginxResult(success=False, error=str(e))
    
    # =========================================================================
    # HTTP Load Balancer
    # =========================================================================
    
    async def setup_http_lb(
        self,
        name: str,
        backends: List[Dict[str, Any]],
        listen_port: int = 80,
        domain: str = None,
        lb_method: str = "least_conn",
        health_check: bool = True,
    ) -> NginxResult:
        """
        Set up nginx HTTP load balancer with upstream config.
        
        Args:
            name: Unique name for this LB config (e.g., "myapp_prod_api")
            backends: List of {"ip": "1.2.3.4", "port": 8000, "weight": 1}
            listen_port: Port nginx listens on (default 80)
            domain: Optional domain for server_name directive
            lb_method: Load balancing method (least_conn, round_robin, ip_hash)
            health_check: Enable health checks via max_fails/fail_timeout
        
        Returns:
            NginxResult with config details
        """
        if not backends:
            self.log("❌ No backends provided for LB")
            return NginxResult(success=False, error="No backends provided")
        
        config_content = self._generate_http_lb_config(
            name, backends, listen_port, domain, lb_method, health_check
        )
        config_path = f"/local/nginx/conf.d/{name}_lb.conf"
        
        try:
            write_result = await self.client.write_file(config_path, config_content)
            if not write_result.success:
                self.log(f"❌ Failed to write config: {write_result.error}")
                return NginxResult(success=False, error=write_result.error)
            
            reload_result = await self.client.reload_nginx()
            if not reload_result.success:
                self.log(f"❌ Nginx reload failed: {reload_result.error}")
                # Remove bad config
                await self.client.write_file(config_path, "# removed due to error")
                return NginxResult(success=False, error=reload_result.error)
            
            self.log(f"✅ Nginx LB configured: {name} → {len(backends)} backends")
            for b in backends:
                self.log(f"   → {b.get('ip')}:{b.get('port', 8000)}")
            
            return NginxResult(
                success=True,
                data={
                    "name": name,
                    "config_path": config_path,
                    "upstream": f"{name}_upstream",
                    "backends": backends,
                    "listen_port": listen_port,
                    "domain": domain,
                }
            )
            
        except Exception as e:
            self.log(f"❌ Nginx LB setup failed: {e}")
            return NginxResult(success=False, error=str(e))
    
    async def remove_http_lb(self, name: str) -> NginxResult:
        """Remove an nginx HTTP load balancer config."""
        try:
            config_path = f"/local/nginx/conf.d/{name}_lb.conf"
            await self.client.write_file(config_path, "# removed")
            await self.client.reload_nginx()
            
            self.log(f"✅ Removed nginx LB: {name}")
            return NginxResult(success=True)
            
        except Exception as e:
            self.log(f"⚠️ Failed to remove nginx LB: {e}")
            return NginxResult(success=False, error=str(e))
    
    async def update_http_lb_backends(
        self,
        name: str,
        backends: List[Dict[str, Any]],
        listen_port: int = 80,
        domain: str = None,
        lb_method: str = "least_conn",
    ) -> NginxResult:
        """Update backends for an existing nginx HTTP LB."""
        return await self.setup_http_lb(
            name=name,
            backends=backends,
            listen_port=listen_port,
            domain=domain,
            lb_method=lb_method,
        )
    
    # =========================================================================
    # Helper: Ensure Data Directories
    # =========================================================================
    
    async def ensure_data_directories(self, volumes: List[str]) -> None:
        """Create host directories for volume mounts."""
        for vol in volumes:
            # Parse volume mount: "/host/path:/container/path[:ro]"
            parts = vol.split(":")
            if len(parts) >= 2:
                host_path = parts[0]
                try:
                    await self.client.create_directory(host_path)
                except:
                    pass  # Directory might already exist
    
    # =========================================================================
    # Config Generation Helpers
    # =========================================================================
    
    def _generate_stream_sidecar(
        self, name: str, port: int, backends_block: str, mode: str
    ) -> str:
        """Generate TCP stream sidecar config."""
        return f'''# Stream config for {name}
# Mode: {mode}
# Apps connect to nginx:{port} (Docker DNS) or localhost:{port}

upstream {name} {{
    least_conn;
{backends_block}
}}

server {{
    listen {port};
    proxy_pass {name};
    proxy_timeout 300s;
    proxy_connect_timeout 10s;
    proxy_next_upstream_timeout 5s;
    proxy_next_upstream_tries 3;
}}
'''
    
    def _generate_http_sidecar(
        self, name: str, port: int, backends_block: str, mode: str
    ) -> str:
        """Generate HTTP sidecar config."""
        return f'''# HTTP config for {name}
# Mode: {mode}
# Internal service discovery

upstream {name} {{
    least_conn;
{backends_block}
}}

server {{
    listen {port};
    server_name _;
    
    location / {{
        proxy_pass http://{name};
        proxy_http_version 1.1;
        
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        
        proxy_connect_timeout 60s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
        
        proxy_next_upstream error timeout http_502 http_503 http_504;
        proxy_next_upstream_timeout 10s;
        proxy_next_upstream_tries 3;
    }}
}}
'''
    
    def _generate_http_lb_config(
        self,
        name: str,
        backends: List[Dict[str, Any]],
        listen_port: int,
        domain: str,
        lb_method: str,
        health_check: bool,
    ) -> str:
        """Generate HTTP load balancer config."""
        upstream_name = f"{name}_upstream"
        
        # LB directive
        lb_directive = ""
        if lb_method == "least_conn":
            lb_directive = "    least_conn;"
        elif lb_method == "ip_hash":
            lb_directive = "    ip_hash;"
        
        # Backend lines
        backend_lines = []
        for b in backends:
            ip = b.get("ip")
            port = b.get("port", 8000)
            weight = b.get("weight", 1)
            
            line = f"    server {ip}:{port}"
            if weight != 1:
                line += f" weight={weight}"
            if health_check:
                line += " max_fails=3 fail_timeout=30s"
            line += ";"
            backend_lines.append(line)
        
        # Server name
        server_name = f"server_name {domain};" if domain else "server_name _;"
        
        return f"""# HTTP Load Balancer: {name}
# Backends: {len(backends)}
# Method: {lb_method}
# Generated by deploy_api

upstream {upstream_name} {{
{lb_directive}
{chr(10).join(backend_lines)}
}}

server {{
    listen {listen_port};
    {server_name}
    
    location / {{
        proxy_pass http://{upstream_name};
        proxy_http_version 1.1;
        
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        
        proxy_connect_timeout 60s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
        
        proxy_buffering off;
        proxy_request_buffering off;
        
        # Try next backend on failure
        proxy_next_upstream error timeout http_502 http_503 http_504;
        proxy_next_upstream_timeout 10s;
        proxy_next_upstream_tries 3;
    }}
    
    location /health {{
        access_log off;
        return 200 "OK";
        add_header Content-Type text/plain;
    }}
}}
"""
