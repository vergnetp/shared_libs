"""
Nginx Configuration Generator.

Generates nginx configs for routing traffic to containers.
Supports:
- HTTP/HTTPS routing
- Load balancing across multiple backends
- SSL termination
- WebSocket proxying
- Rate limiting
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, Any, List, Optional
from enum import Enum

if TYPE_CHECKING:
    from ..context import DeploymentContext
    from ..core.service import Service


class LoadBalanceMethod(Enum):
    """Load balancing methods."""
    ROUND_ROBIN = "round_robin"
    LEAST_CONN = "least_conn"
    IP_HASH = "ip_hash"


@dataclass
class Backend:
    """Backend server for upstream."""
    host: str
    port: int
    weight: int = 1
    max_fails: int = 3
    fail_timeout: str = "30s"
    backup: bool = False
    
    def to_nginx(self) -> str:
        parts = [f"server {self.host}:{self.port}"]
        if self.weight != 1:
            parts.append(f"weight={self.weight}")
        parts.append(f"max_fails={self.max_fails}")
        parts.append(f"fail_timeout={self.fail_timeout}")
        if self.backup:
            parts.append("backup")
        return " ".join(parts) + ";"


@dataclass
class Location:
    """Nginx location block."""
    path: str = "/"
    upstream: Optional[str] = None
    proxy_pass: Optional[str] = None
    
    # Proxy settings
    proxy_http_version: str = "1.1"
    proxy_set_headers: Dict[str, str] = field(default_factory=dict)
    proxy_read_timeout: str = "60s"
    proxy_connect_timeout: str = "60s"
    proxy_send_timeout: str = "60s"
    
    # WebSocket support
    websocket: bool = False
    
    # Rate limiting
    rate_limit: Optional[str] = None  # e.g., "10r/s"
    rate_limit_burst: int = 20
    
    # Static files
    root: Optional[str] = None
    try_files: Optional[str] = None
    
    # Custom directives
    extra: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        if not self.proxy_set_headers:
            self.proxy_set_headers = {
                "Host": "$host",
                "X-Real-IP": "$remote_addr",
                "X-Forwarded-For": "$proxy_add_x_forwarded_for",
                "X-Forwarded-Proto": "$scheme",
            }
        
        if self.websocket:
            self.proxy_set_headers["Upgrade"] = "$http_upgrade"
            self.proxy_set_headers["Connection"] = '"upgrade"'
    
    def to_nginx(self) -> str:
        lines = [f"location {self.path} {{"]
        
        # Proxy pass
        if self.upstream:
            lines.append(f"    proxy_pass http://{self.upstream};")
        elif self.proxy_pass:
            lines.append(f"    proxy_pass {self.proxy_pass};")
        
        # Static files
        if self.root:
            lines.append(f"    root {self.root};")
        if self.try_files:
            lines.append(f"    try_files {self.try_files};")
        
        # Proxy settings
        if self.upstream or self.proxy_pass:
            lines.append(f"    proxy_http_version {self.proxy_http_version};")
            lines.append(f"    proxy_read_timeout {self.proxy_read_timeout};")
            lines.append(f"    proxy_connect_timeout {self.proxy_connect_timeout};")
            lines.append(f"    proxy_send_timeout {self.proxy_send_timeout};")
            
            for header, value in self.proxy_set_headers.items():
                lines.append(f"    proxy_set_header {header} {value};")
        
        # Rate limiting
        if self.rate_limit:
            lines.append(f"    limit_req zone=api burst={self.rate_limit_burst} nodelay;")
        
        # Extra directives
        for directive in self.extra:
            lines.append(f"    {directive}")
        
        lines.append("}")
        return "\n".join(lines)


@dataclass
class ServerBlock:
    """Nginx server block."""
    server_name: str
    listen_port: int = 80
    ssl: bool = False
    ssl_certificate: Optional[str] = None
    ssl_certificate_key: Optional[str] = None
    
    locations: List[Location] = field(default_factory=list)
    
    # SSL settings
    ssl_protocols: str = "TLSv1.2 TLSv1.3"
    ssl_ciphers: str = "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256"
    ssl_prefer_server_ciphers: bool = True
    
    # Security headers
    add_security_headers: bool = True
    
    # Logging
    access_log: Optional[str] = None
    error_log: Optional[str] = None
    
    def to_nginx(self) -> str:
        lines = ["server {"]
        
        # Listen
        if self.ssl:
            lines.append(f"    listen {self.listen_port} ssl http2;")
            lines.append(f"    listen [::]:{self.listen_port} ssl http2;")
        else:
            lines.append(f"    listen {self.listen_port};")
            lines.append(f"    listen [::]:{self.listen_port};")
        
        # Server name
        lines.append(f"    server_name {self.server_name};")
        
        # SSL
        if self.ssl:
            if self.ssl_certificate:
                lines.append(f"    ssl_certificate {self.ssl_certificate};")
            if self.ssl_certificate_key:
                lines.append(f"    ssl_certificate_key {self.ssl_certificate_key};")
            lines.append(f"    ssl_protocols {self.ssl_protocols};")
            lines.append(f"    ssl_ciphers {self.ssl_ciphers};")
            if self.ssl_prefer_server_ciphers:
                lines.append("    ssl_prefer_server_ciphers on;")
            lines.append("    ssl_session_cache shared:SSL:10m;")
            lines.append("    ssl_session_timeout 10m;")
        
        # Security headers
        if self.add_security_headers:
            lines.append("    add_header X-Frame-Options SAMEORIGIN;")
            lines.append("    add_header X-Content-Type-Options nosniff;")
            lines.append("    add_header X-XSS-Protection \"1; mode=block\";")
            if self.ssl:
                lines.append('    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;')
        
        # Logging
        if self.access_log:
            lines.append(f"    access_log {self.access_log};")
        if self.error_log:
            lines.append(f"    error_log {self.error_log};")
        
        # Locations
        for location in self.locations:
            lines.append("")
            for line in location.to_nginx().split("\n"):
                lines.append(f"    {line}")
        
        lines.append("}")
        return "\n".join(lines)


@dataclass
class Upstream:
    """Nginx upstream block."""
    name: str
    backends: List[Backend] = field(default_factory=list)
    method: LoadBalanceMethod = LoadBalanceMethod.ROUND_ROBIN
    keepalive: int = 32
    
    def to_nginx(self) -> str:
        lines = [f"upstream {self.name} {{"]
        
        if self.method == LoadBalanceMethod.LEAST_CONN:
            lines.append("    least_conn;")
        elif self.method == LoadBalanceMethod.IP_HASH:
            lines.append("    ip_hash;")
        
        for backend in self.backends:
            lines.append(f"    {backend.to_nginx()}")
        
        lines.append(f"    keepalive {self.keepalive};")
        lines.append("}")
        return "\n".join(lines)


class NginxConfigGenerator:
    """
    Generate nginx configurations for deployments.
    
    Usage:
        gen = NginxConfigGenerator(ctx)
        
        # Generate config for a service
        config = gen.generate_service_config(
            service=api_service,
            backends=["10.0.0.1:8000", "10.0.0.2:8000"],
            domain="api.example.com",
            ssl=True,
        )
        
        # Generate full site config
        config = gen.generate_site_config(services, domain="example.com")
    """
    
    def __init__(self, ctx: 'DeploymentContext'):
        self.ctx = ctx
    
    def generate_upstream(
        self,
        name: str,
        backends: List[str],  # ["host:port", ...]
        method: LoadBalanceMethod = LoadBalanceMethod.ROUND_ROBIN,
    ) -> Upstream:
        """Generate upstream block."""
        backend_objs = []
        for backend in backends:
            if ":" in backend:
                host, port = backend.rsplit(":", 1)
                backend_objs.append(Backend(host=host, port=int(port)))
            else:
                backend_objs.append(Backend(host=backend, port=80))
        
        return Upstream(name=name, backends=backend_objs, method=method)
    
    def generate_service_config(
        self,
        service: 'Service',
        backends: List[str],
        domain: Optional[str] = None,
        ssl: bool = False,
        ssl_cert: Optional[str] = None,
        ssl_key: Optional[str] = None,
        websocket: bool = False,
    ) -> str:
        """
        Generate nginx config for a service.
        
        Args:
            service: Service definition
            backends: List of backend addresses (host:port)
            domain: Domain name (default: service.domain or localhost)
            ssl: Enable SSL
            ssl_cert: Path to SSL certificate
            ssl_key: Path to SSL key
            websocket: Enable WebSocket support
            
        Returns:
            Nginx configuration string
        """
        domain = domain or service.domain or "localhost"
        upstream_name = f"{self.ctx.namespace}_{service.name}"
        
        # Create upstream
        upstream = self.generate_upstream(upstream_name, backends)
        
        # Create location
        location = Location(
            path="/",
            upstream=upstream_name,
            websocket=websocket,
        )
        
        # Create server block
        server = ServerBlock(
            server_name=domain,
            listen_port=443 if ssl else 80,
            ssl=ssl,
            ssl_certificate=ssl_cert,
            ssl_certificate_key=ssl_key,
            locations=[location],
            access_log=f"/var/log/nginx/{upstream_name}_access.log",
            error_log=f"/var/log/nginx/{upstream_name}_error.log",
        )
        
        # Combine
        parts = [
            "# Auto-generated nginx config",
            f"# Service: {service.name}",
            f"# Generated for: {self.ctx.namespace}",
            "",
            upstream.to_nginx(),
            "",
            server.to_nginx(),
        ]
        
        # Add HTTP redirect if SSL
        if ssl:
            redirect = ServerBlock(
                server_name=domain,
                listen_port=80,
                ssl=False,
                locations=[Location(
                    path="/",
                    extra=["return 301 https://$server_name$request_uri;"],
                )],
            )
            parts.append("")
            parts.append("# HTTP to HTTPS redirect")
            parts.append(redirect.to_nginx())
        
        return "\n".join(parts)
    
    def generate_site_config(
        self,
        services: Dict[str, 'Service'],
        backends_map: Dict[str, List[str]],  # service_name -> [backends]
        domain: str,
        ssl: bool = False,
        ssl_cert: Optional[str] = None,
        ssl_key: Optional[str] = None,
    ) -> str:
        """
        Generate nginx config for multiple services under one domain.
        
        Routes by path prefix:
        - /api/* -> api service
        - /ws/* -> websocket service
        - /* -> frontend service
        
        Args:
            services: Dict of service name -> Service
            backends_map: Dict of service name -> backend addresses
            domain: Domain name
            ssl: Enable SSL
            
        Returns:
            Nginx configuration string
        """
        upstreams = []
        locations = []
        
        for name, service in services.items():
            backends = backends_map.get(name, [])
            if not backends:
                continue
            
            upstream_name = f"{self.ctx.namespace}_{name}"
            upstreams.append(self.generate_upstream(upstream_name, backends))
            
            # Determine path based on service name/type
            if name in ("api", "backend"):
                path = "/api/"
            elif name in ("ws", "websocket"):
                path = "/ws/"
                locations.append(Location(
                    path=path,
                    upstream=upstream_name,
                    websocket=True,
                ))
                continue
            elif name in ("frontend", "web", "ui"):
                path = "/"
            else:
                path = f"/{name}/"
            
            locations.append(Location(path=path, upstream=upstream_name))
        
        # Create server block
        server = ServerBlock(
            server_name=domain,
            listen_port=443 if ssl else 80,
            ssl=ssl,
            ssl_certificate=ssl_cert,
            ssl_certificate_key=ssl_key,
            locations=locations,
            access_log=f"/var/log/nginx/{self.ctx.namespace}_access.log",
            error_log=f"/var/log/nginx/{self.ctx.namespace}_error.log",
        )
        
        # Combine
        parts = [
            "# Auto-generated nginx config",
            f"# Site: {domain}",
            f"# Project: {self.ctx.namespace}",
            "",
        ]
        
        for upstream in upstreams:
            parts.append(upstream.to_nginx())
            parts.append("")
        
        parts.append(server.to_nginx())
        
        # Add HTTP redirect if SSL
        if ssl:
            redirect = ServerBlock(
                server_name=domain,
                listen_port=80,
                ssl=False,
                locations=[Location(
                    path="/",
                    extra=["return 301 https://$server_name$request_uri;"],
                )],
            )
            parts.append("")
            parts.append("# HTTP to HTTPS redirect")
            parts.append(redirect.to_nginx())
        
        return "\n".join(parts)
    
    def generate_default_config(self) -> str:
        """Generate default nginx.conf."""
        return """# Auto-generated nginx.conf
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {
    worker_connections 1024;
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

    # Gzip
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css text/xml application/json application/javascript 
               application/xml application/xml+rss text/javascript;

    # Rate limiting zone
    limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;

    # Include site configs
    include /etc/nginx/conf.d/*.conf;
}
"""
