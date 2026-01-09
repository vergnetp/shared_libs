"""
Domain Service - Automatic domain provisioning for deployments.

Handles:
- Auto-generating subdomains from container names
- Creating Cloudflare DNS records
- Configuring nginx virtual hosts with HTTPS (using Cloudflare Origin Certificate)
- Custom domain aliases

Usage:
    domain_svc = DomainService(
        cloudflare_token="...",
        base_domain="digitalpixo.com",
        origin_cert="-----BEGIN CERTIFICATE-----...",
        origin_key="-----BEGIN PRIVATE KEY-----...",
    )
    
    # Auto-provision domain for a deployment
    result = await domain_svc.provision_domain(
        container_name="fc153d_ai_prod_api",
        server_ips=["1.2.3.4", "5.6.7.8"],
        container_port=8002,
        agent_client_factory=lambda ip: NodeAgentClient(ip, api_key),
    )
    # Returns: DomainResult(domain="fc153d-ai-prod-api.digitalpixo.com", ...)
    
    # Add custom domain alias
    await domain_svc.add_domain_alias(
        primary_domain="fc153d-ai-prod-api.digitalpixo.com",
        alias_domain="api.myclient.com",
        server_ips=["1.2.3.4"],
        agent_client_factory=...,
    )
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable, Awaitable
import re

from ..cloud.cloudflare import CloudflareClient, CloudflareError


# Cloudflare Origin Certificate paths on servers
# SSL cert paths (host vs container)
# Write files to HOST path, but nginx config references CONTAINER path
HOST_CERT_PATH = "/local/nginx/certs/origin.pem"
HOST_KEY_PATH = "/local/nginx/certs/origin.key"
NGINX_CERT_PATH = "/etc/nginx/certs/origin.pem"  # Inside container
NGINX_KEY_PATH = "/etc/nginx/certs/origin.key"   # Inside container


@dataclass
class DomainResult:
    """Result of domain provisioning."""
    success: bool
    domain: Optional[str] = None  # The provisioned domain
    dns_created: bool = False     # Whether DNS record was created
    nginx_configured: bool = False  # Whether nginx was configured
    ssl_configured: bool = False   # Whether SSL cert was deployed
    server_ips: List[str] = field(default_factory=list)
    error: Optional[str] = None
    aliases: List[str] = field(default_factory=list)  # Custom domain aliases


class DomainService:
    """
    Automatic domain provisioning for deployments.
    
    Generates subdomains like: {workspace}-{project}-{env}-{service}.{base_domain}
    Creates DNS records pointing to all server IPs.
    Configures nginx virtual hosts with HTTPS on each server.
    
    SSL certificates are fetched automatically from vault (Infisical or env vars).
    """
    
    DEFAULT_BASE_DOMAIN = "digitalpixo.com"
    
    def __init__(
        self,
        cloudflare_token: str,
        base_domain: str = None,
        log: Callable[[str], None] = None,
    ):
        """
        Initialize domain service.
        
        Args:
            cloudflare_token: Cloudflare API token with DNS edit permissions
            base_domain: Base domain for subdomains (default: digitalpixo.com)
            log: Optional logging callback
        """
        self.cf = CloudflareClient(cloudflare_token)
        self.base_domain = base_domain or self.DEFAULT_BASE_DOMAIN
        self.log = log or (lambda msg: None)
        
        # Fetch certs from vault
        from ..utils.vault import get_origin_cert, get_origin_key
        self.origin_cert = get_origin_cert()
        self.origin_key = get_origin_key()
        
        # Debug
        if self.origin_cert:
            self.log(f"   🔐 Origin certificate loaded ({len(self.origin_cert)} bytes)")
        else:
            self.log(f"   ⚠️ No origin certificate found - HTTPS will be disabled")
    
    def container_name_to_subdomain(self, container_name: str) -> str:
        """
        Convert container name to valid subdomain.
        
        Container name format: {workspace}_{project}_{env}_{service}
        Subdomain format: {workspace}-{project}-{env}-{service}
        
        Args:
            container_name: Container name (e.g., "fc153d_ai_prod_api")
            
        Returns:
            Subdomain string (e.g., "fc153d-ai-prod-api")
        """
        # Replace underscores with hyphens
        subdomain = container_name.replace("_", "-")
        
        # Ensure valid DNS name (lowercase, alphanumeric + hyphens)
        subdomain = re.sub(r'[^a-z0-9-]', '', subdomain.lower())
        
        # Remove leading/trailing hyphens
        subdomain = subdomain.strip("-")
        
        # Limit length (max 63 chars for DNS label)
        if len(subdomain) > 63:
            subdomain = subdomain[:63].rstrip("-")
        
        return subdomain
    
    def get_full_domain(self, container_name: str) -> str:
        """
        Get full domain for a container.
        
        Args:
            container_name: Container name
            
        Returns:
            Full domain (e.g., "fc153d-ai-prod-api.digitalpixo.com")
        """
        subdomain = self.container_name_to_subdomain(container_name)
        return f"{subdomain}.{self.base_domain}"
    
    async def _ensure_ssl_cert(
        self,
        agent: Any,
        ip: str,
    ) -> bool:
        """
        Ensure SSL certificate is deployed to server.
        
        Args:
            agent: NodeAgentClient for the server
            ip: Server IP (for logging)
            
        Returns:
            True if cert is ready
        """
        if not self.origin_cert or not self.origin_key:
            self.log(f"   ⚠️ No origin certificate configured - HTTPS disabled")
            return False
        
        try:
            self.log(f"   📜 Deploying SSL certificate to {ip}...")
            
            # Ensure certs directory exists
            dir_result = await agent.create_directory("/local/nginx/certs")
            if not dir_result.success:
                self.log(f"   ⚠️ Failed to create certs directory on {ip}: {dir_result.error}")
                return False
            
            # Write certificate
            cert_result = await agent.write_file(HOST_CERT_PATH, self.origin_cert, permissions="644")
            if not cert_result.success:
                self.log(f"   ⚠️ Failed to write certificate on {ip}: {cert_result.error}")
                return False
            
            # Write key
            key_result = await agent.write_file(HOST_KEY_PATH, self.origin_key, permissions="600")
            if not key_result.success:
                self.log(f"   ⚠️ Failed to write key on {ip}: {key_result.error}")
                return False
            
            self.log(f"   ✅ SSL certificate deployed to {ip}")
            return True
            
        except Exception as e:
            self.log(f"   ⚠️ Failed to deploy SSL cert to {ip}: {e}")
            return False
            return False
    
    async def provision_domain(
        self,
        container_name: str,
        server_ips: List[str],
        container_port: int,
        agent_client_factory: Callable[[str], Any],
        proxied: bool = True,
    ) -> DomainResult:
        """
        Provision domain for a deployment.
        
        1. Generate subdomain from container name
        2. Create/update Cloudflare DNS A records for each server IP
        3. Deploy SSL certificate to servers (if configured)
        4. Configure nginx virtual host with HTTPS on each server
        
        Args:
            container_name: Container name (e.g., "fc153d_ai_prod_api")
            server_ips: List of server IPs running this container
            container_port: Port the container listens on
            agent_client_factory: Factory to create NodeAgentClient for an IP
            proxied: Whether to proxy through Cloudflare (recommended)
            
        Returns:
            DomainResult with provisioning status
        """
        domain = self.get_full_domain(container_name)
        self.log(f"🌐 Provisioning domain: {domain}")
        
        result = DomainResult(success=False, domain=domain, server_ips=server_ips)
        
        # Step 1: Create DNS records
        try:
            self.log(f"   Creating DNS records for {len(server_ips)} server(s)...")
            
            for ip in server_ips:
                self.cf.upsert_a_record(
                    domain=domain,
                    ip=ip,
                    proxied=proxied,
                )
            
            result.dns_created = True
            self.log(f"   ✅ DNS: {domain} → {', '.join(server_ips)}")
            
        except CloudflareError as e:
            result.error = f"DNS creation failed: {e.message}"
            self.log(f"   ❌ DNS failed: {e.message}")
            return result
        except Exception as e:
            result.error = f"DNS creation failed: {e}"
            self.log(f"   ❌ DNS failed: {e}")
            return result
        
        # Step 2: Deploy SSL certificates and configure nginx
        try:
            self.log(f"   Configuring nginx virtual hosts with HTTPS...")
            
            ssl_enabled = bool(self.origin_cert and self.origin_key)
            
            # Deploy to each server
            for ip in server_ips:
                agent = agent_client_factory(ip)
                
                # Deploy SSL cert if configured
                server_ssl_ok = False
                if ssl_enabled:
                    server_ssl_ok = await self._ensure_ssl_cert(agent, ip)
                    if server_ssl_ok:
                        result.ssl_configured = True
                
                # Generate config for this server (with or without SSL based on cert success)
                server_nginx_config = self._generate_nginx_vhost(
                    domain=domain,
                    container_name=container_name,
                    container_port=container_port,
                    upstream_ips=server_ips,
                    ssl_enabled=server_ssl_ok,  # Only enable SSL if cert was deployed
                )
                
                # Write nginx config file
                config_path = f"/local/nginx/conf.d/{domain}.conf"
                write_result = await agent.write_file(config_path, server_nginx_config)
                
                if not write_result.success:
                    self.log(f"   ⚠️ Failed to write nginx config on {ip}")
                    continue
                
                # Test nginx config
                test_result = await agent.test_nginx_config()
                if not test_result.success:
                    self.log(f"   ⚠️ Nginx config test failed on {ip}: {test_result.error}")
                    # Remove bad config
                    await agent.delete_file(config_path)
                    continue
                
                # Reload nginx
                reload_result = await agent.reload_nginx()
                if not reload_result.success:
                    self.log(f"   ⚠️ Nginx reload failed on {ip}")
                    continue
            
            result.nginx_configured = True
            self.log(f"   ✅ Nginx configured on all servers (HTTPS: {'enabled' if ssl_enabled else 'disabled'})")
            
        except Exception as e:
            result.error = f"Nginx configuration failed: {e}"
            self.log(f"   ❌ Nginx config failed: {e}")
            # Don't return - DNS is already set up
        
        result.success = result.dns_created
        
        if result.success:
            self.log(f"   🎉 Domain ready: https://{domain}")
        
        return result
    
    async def add_domain_alias(
        self,
        primary_domain: str,
        alias_domain: str,
        server_ips: List[str],
        container_name: str,
        container_port: int,
        agent_client_factory: Callable[[str], Any],
        create_dns: bool = False,
    ) -> DomainResult:
        """
        Add a custom domain alias (e.g., client's own domain).
        
        Args:
            primary_domain: The primary domain (e.g., "fc153d-ai-prod-api.digitalpixo.com")
            alias_domain: The custom domain to add (e.g., "api.myclient.com")
            server_ips: List of server IPs
            container_name: Container name
            container_port: Container port
            agent_client_factory: Factory to create NodeAgentClient
            create_dns: If True, create DNS record (only for your domains)
            
        Returns:
            DomainResult
        """
        self.log(f"🌐 Adding domain alias: {alias_domain} → {primary_domain}")
        
        result = DomainResult(
            success=False,
            domain=alias_domain,
            server_ips=server_ips,
        )
        
        # Optionally create DNS (only for domains we control)
        if create_dns:
            try:
                for ip in server_ips:
                    self.cf.upsert_a_record(domain=alias_domain, ip=ip, proxied=True)
                result.dns_created = True
                self.log(f"   ✅ DNS created for {alias_domain}")
            except Exception as e:
                self.log(f"   ⚠️ DNS creation skipped: {e}")
        
        # Update nginx config to include alias
        try:
            ssl_enabled = bool(self.origin_cert and self.origin_key)
            
            nginx_config = self._generate_nginx_vhost(
                domain=primary_domain,
                container_name=container_name,
                container_port=container_port,
                upstream_ips=server_ips,
                aliases=[alias_domain],
                ssl_enabled=ssl_enabled,
            )
            
            for ip in server_ips:
                agent = agent_client_factory(ip)
                config_path = f"/local/nginx/conf.d/{primary_domain}.conf"
                
                await agent.write_file(config_path, nginx_config)
                await agent.reload_nginx()
            
            result.nginx_configured = True
            result.aliases = [alias_domain]
            self.log(f"   ✅ Nginx updated with alias")
            
        except Exception as e:
            result.error = f"Failed to update nginx: {e}"
            self.log(f"   ❌ Nginx update failed: {e}")
            return result
        
        result.success = True
        self.log(f"   🎉 Alias ready: https://{alias_domain}")
        
        return result
    
    async def remove_domain(
        self,
        container_name: str,
        server_ips: List[str],
        agent_client_factory: Callable[[str], Any],
    ) -> bool:
        """
        Remove domain when a deployment is deleted.
        
        Args:
            container_name: Container name
            server_ips: Server IPs to clean up
            agent_client_factory: Factory for NodeAgentClient
            
        Returns:
            True if cleanup succeeded
        """
        domain = self.get_full_domain(container_name)
        self.log(f"🗑️ Removing domain: {domain}")
        
        # Remove DNS records
        try:
            self.cf.delete_record(domain=domain)
            self.log(f"   ✅ DNS record removed")
        except Exception as e:
            self.log(f"   ⚠️ DNS removal failed: {e}")
        
        # Remove nginx configs
        for ip in server_ips:
            try:
                agent = agent_client_factory(ip)
                config_path = f"/local/nginx/conf.d/{domain}.conf"
                await agent.delete_file(config_path)
                await agent.reload_nginx()
            except Exception as e:
                self.log(f"   ⚠️ Nginx cleanup failed on {ip}: {e}")
        
        self.log(f"   ✅ Domain removed")
        return True
    
    def _generate_nginx_vhost(
        self,
        domain: str,
        container_name: str,
        container_port: int,
        upstream_ips: List[str],
        aliases: List[str] = None,
        ssl_enabled: bool = True,
    ) -> str:
        """
        Generate nginx virtual host configuration with HTTPS.
        
        Creates:
        - Upstream block with all server IPs (least_conn)
        - Server block on 443 with SSL (Cloudflare Origin Cert)
        - Server block on 80 redirecting to HTTPS
        - Proxy pass to upstream
        
        Args:
            domain: Primary domain
            container_name: Container name (for upstream naming)
            container_port: Port the container listens on
            upstream_ips: List of backend server IPs
            aliases: Optional list of domain aliases
            ssl_enabled: Whether to enable HTTPS
            
        Returns:
            Nginx configuration string
        """
        # Create safe upstream name
        upstream_name = container_name.replace("-", "_").replace(".", "_")
        
        # Build upstream block with all backends
        upstream_servers = "\n".join([
            f"    server {ip}:{container_port};"
            for ip in upstream_ips
        ])
        
        # Server names (primary + aliases)
        server_names = [domain]
        if aliases:
            server_names.extend(aliases)
        server_name_str = " ".join(server_names)
        
        # Cloudflare IP ranges for real_ip
        cloudflare_ips = """    # Cloudflare real IP
    set_real_ip_from 103.21.244.0/22;
    set_real_ip_from 103.22.200.0/22;
    set_real_ip_from 103.31.4.0/22;
    set_real_ip_from 104.16.0.0/13;
    set_real_ip_from 104.24.0.0/14;
    set_real_ip_from 108.162.192.0/18;
    set_real_ip_from 131.0.72.0/22;
    set_real_ip_from 141.101.64.0/18;
    set_real_ip_from 162.158.0.0/15;
    set_real_ip_from 172.64.0.0/13;
    set_real_ip_from 173.245.48.0/20;
    set_real_ip_from 188.114.96.0/20;
    set_real_ip_from 190.93.240.0/20;
    set_real_ip_from 197.234.240.0/22;
    set_real_ip_from 198.41.128.0/17;
    real_ip_header CF-Connecting-IP;"""
        
        # Proxy settings block
        proxy_settings = f"""        proxy_pass http://{upstream_name};
        proxy_http_version 1.1;
        
        # Headers
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        
        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
        
        # Buffering
        proxy_buffering on;
        proxy_buffer_size 4k;
        proxy_buffers 8 4k;"""
        
        if ssl_enabled:
            config = f"""# Auto-generated by DomainService
# Domain: {domain}
# Container: {container_name}
# SSL: Cloudflare Origin Certificate

upstream {upstream_name} {{
    least_conn;
{upstream_servers}
}}

# HTTPS server
server {{
    listen 443 ssl;
    http2 on;
    server_name {server_name_str};
    
    # Cloudflare Origin Certificate
    ssl_certificate {NGINX_CERT_PATH};
    ssl_certificate_key {NGINX_KEY_PATH};
    
    # SSL settings
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    
{cloudflare_ips}
    
    location / {{
{proxy_settings}
    }}
    
    # Health check endpoint
    location /health {{
        access_log off;
        proxy_pass http://{upstream_name};
        proxy_connect_timeout 5s;
        proxy_read_timeout 5s;
    }}
}}

# HTTP redirect to HTTPS
server {{
    listen 80;
    server_name {server_name_str};
    
{cloudflare_ips}
    
    # For Cloudflare, just proxy (CF handles HTTPS to client)
    location / {{
{proxy_settings}
    }}
}}
"""
        else:
            # HTTP only (no SSL cert configured)
            config = f"""# Auto-generated by DomainService
# Domain: {domain}
# Container: {container_name}
# SSL: Disabled (no origin certificate)

upstream {upstream_name} {{
    least_conn;
{upstream_servers}
}}

server {{
    listen 80;
    server_name {server_name_str};
    
{cloudflare_ips}
    
    location / {{
{proxy_settings}
    }}
    
    # Health check endpoint
    location /health {{
        access_log off;
        proxy_pass http://{upstream_name};
        proxy_connect_timeout 5s;
        proxy_read_timeout 5s;
    }}
}}
"""
        return config
