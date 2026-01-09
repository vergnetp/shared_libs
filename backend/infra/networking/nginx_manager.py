"""
Nginx Manager - Manage nginx containers and configurations.

Handles:
- HTTP/HTTPS config generation (for web services)
- TCP stream config generation (for postgres, redis, etc.)
- Nginx container deployment and management
- Config reload without downtime
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Literal
from enum import Enum

from .ports import DeploymentPortResolver


class BackendMode(Enum):
    """How nginx routes to backends."""
    CONTAINER = "container"  # Use container names (single server, Docker DNS)
    IP_PORT = "ip_port"      # Use IP:port (multi-server)


@dataclass
class StreamBackend:
    """Backend for TCP stream proxy."""
    host: str           # Container name or IP
    port: int           # Port to connect to
    weight: int = 1
    max_fails: int = 3
    fail_timeout: str = "30s"
    
    def to_nginx(self) -> str:
        parts = [f"server {self.host}:{self.port}"]
        if self.weight != 1:
            parts.append(f"weight={self.weight}")
        parts.append(f"max_fails={self.max_fails}")
        parts.append(f"fail_timeout={self.fail_timeout}")
        return " ".join(parts) + ";"


@dataclass  
class StreamConfig:
    """TCP stream configuration for a service."""
    name: str                          # Upstream name
    listen_port: int                   # Internal port nginx listens on
    backends: List[StreamBackend] = field(default_factory=list)
    
    def to_nginx(self) -> str:
        """Generate nginx stream config."""
        lines = [
            f"# Stream config for {self.name}",
            f"upstream {self.name} {{",
            "    least_conn;",
        ]
        
        for backend in self.backends:
            lines.append(f"    {backend.to_nginx()}")
        
        lines.append("}")
        lines.append("")
        lines.append("server {")
        lines.append(f"    listen {self.listen_port};")
        lines.append(f"    proxy_pass {self.name};")
        lines.append("    proxy_timeout 300s;")
        lines.append("    proxy_connect_timeout 10s;")
        lines.append("}")
        
        return "\n".join(lines)


class NginxManager:
    """
    Manage nginx for deployments.
    
    Handles two types of routing:
    
    1. HTTP routing (web services):
       - Domain-based routing
       - SSL termination
       - Path-based routing (/api, /ws, etc.)
    
    2. TCP stream routing (databases, caches):
       - Internal service mesh
       - Apps connect to localhost:INTERNAL_PORT
       - Nginx routes to actual backends
    
    Usage:
        mgr = NginxManager()
        
        # Generate stream config for postgres
        config = mgr.generate_stream_config(
            user="u1",
            project="myapp",
            env="prod",
            service="postgres",
            backends=[
                {"host": "myapp_prod_postgres", "port": 5432},  # Container mode
                # OR {"host": "10.0.0.5", "port": 8357},        # IP mode
            ],
            mode=BackendMode.CONTAINER,
        )
        
        # Get container run command
        cmd = mgr.get_nginx_run_config(project="myapp", env="prod")
    """
    
    # Nginx container settings
    NGINX_IMAGE = "nginx:alpine"
    NGINX_CONTAINER_PREFIX = "nginx"
    
    # Config directories (inside container)
    HTTP_CONF_DIR = "/etc/nginx/conf.d"
    STREAM_CONF_DIR = "/etc/nginx/stream.d"
    
    # Host config directories
    HOST_CONF_BASE = "/local/nginx"
    
    def __init__(self, host_conf_base: str = None):
        self.host_conf_base = host_conf_base or self.HOST_CONF_BASE
    
    # =========================================================================
    # Stream Config Generation (TCP services)
    # =========================================================================
    
    def generate_stream_config(
        self,
        user: str,
        project: str,
        env: str,
        service: str,
        backends: List[Dict],  # [{"host": "...", "port": ...}, ...]
        mode: BackendMode = BackendMode.CONTAINER,
        container_port: int = None,
    ) -> StreamConfig:
        """
        Generate nginx stream config for a TCP service.
        
        Args:
            user: User ID
            project: Project name
            env: Environment
            service: Service name
            backends: List of backend dicts with host/port
            mode: CONTAINER (use container names) or IP_PORT (use IP:hostport)
            container_port: Container port (for determining internal port)
            
        Returns:
            StreamConfig object
        """
        # Get internal port (stable, never changes)
        internal_port = DeploymentPortResolver.get_internal_port(
            user, project, env, service
        )
        
        # Upstream name
        upstream_name = f"{project}_{env}_{service}"
        
        # Build backends
        stream_backends = []
        for b in backends:
            stream_backends.append(StreamBackend(
                host=b["host"],
                port=b["port"],
                weight=b.get("weight", 1),
            ))
        
        return StreamConfig(
            name=upstream_name,
            listen_port=internal_port,
            backends=stream_backends,
        )
    
    def generate_stream_config_file(
        self,
        user: str,
        project: str,
        env: str,
        service: str,
        backends: List[Dict],
        mode: BackendMode = BackendMode.CONTAINER,
    ) -> str:
        """Generate stream config as string for writing to file."""
        config = self.generate_stream_config(
            user, project, env, service, backends, mode
        )
        return config.to_nginx()
    
    # =========================================================================
    # HTTP Config Generation
    # =========================================================================
    
    def generate_http_upstream(
        self,
        name: str,
        backends: List[Dict],  # [{"host": "...", "port": ...}, ...]
        method: str = "least_conn",
    ) -> str:
        """Generate HTTP upstream block."""
        lines = [f"upstream {name} {{"]
        
        if method == "least_conn":
            lines.append("    least_conn;")
        elif method == "ip_hash":
            lines.append("    ip_hash;")
        
        for b in backends:
            host = b["host"]
            port = b["port"]
            weight = b.get("weight", 1)
            line = f"    server {host}:{port}"
            if weight != 1:
                line += f" weight={weight}"
            line += ";"
            lines.append(line)
        
        lines.append("    keepalive 32;")
        lines.append("}")
        
        return "\n".join(lines)
    
    def generate_http_server(
        self,
        server_name: str,
        upstream_name: str,
        listen_port: int = 80,
        ssl: bool = False,
        ssl_cert: str = None,
        ssl_key: str = None,
        websocket: bool = False,
        locations: Dict[str, str] = None,  # {"/api": "api_upstream", "/": "web_upstream"}
    ) -> str:
        """Generate HTTP server block."""
        lines = ["server {"]
        
        # Listen
        if ssl:
            lines.append(f"    listen {listen_port} ssl http2;")
            lines.append(f"    listen [::]:{listen_port} ssl http2;")
        else:
            lines.append(f"    listen {listen_port};")
            lines.append(f"    listen [::]:{listen_port};")
        
        # Server name
        lines.append(f"    server_name {server_name};")
        
        # SSL
        if ssl and ssl_cert and ssl_key:
            lines.append(f"    ssl_certificate {ssl_cert};")
            lines.append(f"    ssl_certificate_key {ssl_key};")
            lines.append("    ssl_protocols TLSv1.2 TLSv1.3;")
            lines.append("    ssl_prefer_server_ciphers on;")
            lines.append("    ssl_session_cache shared:SSL:10m;")
        
        # Locations
        if locations:
            for path, upstream in locations.items():
                lines.append("")
                lines.append(f"    location {path} {{")
                lines.append(f"        proxy_pass http://{upstream};")
                lines.append("        proxy_http_version 1.1;")
                lines.append("        proxy_set_header Host $host;")
                lines.append("        proxy_set_header X-Real-IP $remote_addr;")
                lines.append("        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;")
                lines.append("        proxy_set_header X-Forwarded-Proto $scheme;")
                if websocket or path in ("/ws", "/ws/", "/socket", "/socket.io"):
                    lines.append('        proxy_set_header Upgrade $http_upgrade;')
                    lines.append('        proxy_set_header Connection "upgrade";')
                lines.append("    }")
        else:
            # Default: proxy everything to upstream
            lines.append("")
            lines.append("    location / {")
            lines.append(f"        proxy_pass http://{upstream_name};")
            lines.append("        proxy_http_version 1.1;")
            lines.append("        proxy_set_header Host $host;")
            lines.append("        proxy_set_header X-Real-IP $remote_addr;")
            lines.append("        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;")
            lines.append("        proxy_set_header X-Forwarded-Proto $scheme;")
            if websocket:
                lines.append('        proxy_set_header Upgrade $http_upgrade;')
                lines.append('        proxy_set_header Connection "upgrade";')
            lines.append("    }")
        
        lines.append("}")
        
        return "\n".join(lines)
    
    def generate_http_redirect(self, server_name: str) -> str:
        """Generate HTTP to HTTPS redirect server block."""
        return f"""server {{
    listen 80;
    listen [::]:80;
    server_name {server_name};
    return 301 https://$host$request_uri;
}}"""
    
    def generate_service_http_config(
        self,
        project: str,
        env: str,
        service: str,
        domain: str,
        backends: List[Dict],
        ssl: bool = False,
        ssl_cert: str = None,
        ssl_key: str = None,
        websocket: bool = False,
    ) -> str:
        """
        Generate complete HTTP config for a service.
        
        Args:
            project: Project name
            env: Environment
            service: Service name
            domain: Domain name
            backends: List of backend dicts
            ssl: Enable SSL
            ssl_cert: Path to SSL certificate
            ssl_key: Path to SSL key
            websocket: Enable WebSocket support
            
        Returns:
            Complete nginx config string
        """
        upstream_name = f"{project}_{env}_{service}"
        
        parts = [
            f"# HTTP config for {project}/{env}/{service}",
            f"# Domain: {domain}",
            "",
            self.generate_http_upstream(upstream_name, backends),
            "",
            self.generate_http_server(
                server_name=domain,
                upstream_name=upstream_name,
                listen_port=443 if ssl else 80,
                ssl=ssl,
                ssl_cert=ssl_cert,
                ssl_key=ssl_key,
                websocket=websocket,
            ),
        ]
        
        if ssl:
            parts.append("")
            parts.append("# HTTP to HTTPS redirect")
            parts.append(self.generate_http_redirect(domain))
        
        return "\n".join(parts)
    
    # =========================================================================
    # Main nginx.conf Generation
    # =========================================================================
    
    def generate_main_nginx_conf(self, include_stream: bool = True) -> str:
        """
        Generate main nginx.conf with stream support.
        
        Args:
            include_stream: Include stream block for TCP proxying
            
        Returns:
            Complete nginx.conf content
        """
        conf = """# Auto-generated nginx.conf
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {
    worker_connections 4096;
    use epoll;
    multi_accept on;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for"';

    access_log /var/log/nginx/access.log main;

    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    client_max_body_size 100M;

    # Gzip
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css text/xml application/json application/javascript 
               application/xml application/xml+rss text/javascript;

    # Rate limiting zone
    limit_req_zone $binary_remote_addr zone=api:10m rate=100r/s;

    # Include HTTP site configs
    include /etc/nginx/conf.d/*.conf;
}
"""
        
        if include_stream:
            conf += """
# TCP/UDP stream proxying (for postgres, redis, etc.)
stream {
    # Include stream configs
    include /etc/nginx/stream.d/*.conf;
}
"""
        
        return conf
    
    # =========================================================================
    # Container Management
    # =========================================================================
    
    def get_nginx_container_name(self, project: str = None, env: str = None) -> str:
        """
        Get nginx container name.
        
        For now, one nginx per server (shared across projects).
        Future: could be per-project or per-env.
        """
        # Shared nginx across all projects on server
        return "nginx"
    
    def get_nginx_network_name(self, project: str, env: str) -> str:
        """Get Docker network name for a project/env."""
        return f"{project}_{env}_network"
    
    def get_host_config_paths(self) -> Dict[str, str]:
        """
        Get host paths for nginx config directories.
        
        Returns:
            Dict with paths for conf.d, stream.d, nginx.conf
        """
        return {
            "conf_d": f"{self.host_conf_base}/conf.d",
            "stream_d": f"{self.host_conf_base}/stream.d",
            "nginx_conf": f"{self.host_conf_base}/nginx.conf",
            "certs": f"{self.host_conf_base}/certs",
            "logs": f"{self.host_conf_base}/logs",
        }
    
    def get_nginx_run_config(self) -> Dict:
        """
        Get configuration for running nginx container.
        
        Returns:
            Dict with container run parameters
        """
        paths = self.get_host_config_paths()
        
        return {
            "name": self.get_nginx_container_name(),
            "image": self.NGINX_IMAGE,
            "ports": {
                "80": "80",
                "443": "443",
            },
            "volumes": [
                f"{paths['nginx_conf']}:/etc/nginx/nginx.conf:ro",
                f"{paths['conf_d']}:/etc/nginx/conf.d:ro",
                f"{paths['stream_d']}:/etc/nginx/stream.d:ro",
                f"{paths['certs']}:/etc/nginx/certs:ro",
                f"{paths['logs']}:/var/log/nginx",
            ],
            "restart_policy": "unless-stopped",
        }
    
    def get_stream_config_path(self, project: str, env: str, service: str) -> str:
        """Get host path for a stream config file."""
        paths = self.get_host_config_paths()
        return f"{paths['stream_d']}/{project}_{env}_{service}.conf"
    
    def get_http_config_path(self, project: str, env: str, service: str) -> str:
        """Get host path for an HTTP config file."""
        paths = self.get_host_config_paths()
        return f"{paths['conf_d']}/{project}_{env}_{service}.conf"


# Convenience functions
def get_internal_port(user: str, project: str, env: str, service: str) -> int:
    """Get internal port for service mesh routing."""
    return DeploymentPortResolver.get_internal_port(user, project, env, service)


def get_host_port(user: str, project: str, env: str, service: str, container_port: str) -> int:
    """Get host port for external access."""
    return DeploymentPortResolver.generate_host_port(
        user, project, env, service, container_port
    )
