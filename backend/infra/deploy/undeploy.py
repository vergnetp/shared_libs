"""
Undeploy Service - Container and service removal with cleanup.

Provides two levels of removal:
1. remove_container_from_droplet() - Remove ONE container from ONE droplet
2. remove_service() - Remove service from ALL droplets

Handles:
- Container stop/removal
- Nginx config cleanup
- DNS record updates (multi-server aware)

Usage:
    from infra.deploy.undeploy import UndeployService, AsyncUndeployService
    
    # Sync (scripts)
    service = UndeployService(do_token, cf_token, zone="example.com")
    result = service.remove_container_from_droplet(
        container_name="my-api",
        droplet_ip="1.2.3.4",
        domain="my-api.example.com",
    )
    
    # Async (FastAPI)
    service = AsyncUndeployService(do_token, cf_token, zone="example.com")
    result = await service.remove_container_from_droplet(...)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Callable, Dict, Any
import asyncio


@dataclass
class UndeployResult:
    """Result of undeploy operation."""
    success: bool
    container_removed: bool = False
    nginx_removed: bool = False
    dns_updated: bool = False
    errors: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


class AsyncUndeployService:
    """
    Service for removing containers and services (async).
    
    Handles cleanup of:
    - Docker containers
    - Nginx reverse proxy configs
    - DNS records (Cloudflare)
    """
    
    def __init__(
        self,
        do_token: str,
        cf_token: Optional[str] = None,
        zone: Optional[str] = None,
        log: Optional[Callable[[str], None]] = None,
    ):
        """
        Initialize undeploy service.
        
        Args:
            do_token: DigitalOcean API token (for node agent auth)
            cf_token: Cloudflare API token (optional, for DNS cleanup)
            zone: Cloudflare zone/domain (e.g., "example.com")
            log: Optional logging function
        """
        self.do_token = do_token
        self.cf_token = cf_token
        self.zone = zone
        self._log = log or (lambda x: None)
    
    def log(self, msg: str) -> None:
        """Log a message."""
        self._log(msg)
    
    async def remove_container_from_droplet(
        self,
        container_name: str,
        droplet_ip: str,
        domain: Optional[str] = None,
        cleanup_nginx: bool = True,
        cleanup_dns: bool = False,
    ) -> UndeployResult:
        """
        Remove ONE container from ONE droplet.
        
        Service may continue running on other droplets.
        
        Args:
            container_name: Name of container to remove
            droplet_ip: IP address of the droplet
            domain: Full domain name (e.g., "api.example.com") for DNS cleanup
            cleanup_nginx: Remove nginx config (default True)
            cleanup_dns: Update DNS records (default False, requires cf_token)
            
        Returns:
            UndeployResult with status of each cleanup step
        """
        from ..node_agent import NodeAgentClient
        
        result = UndeployResult(success=True)
        agent = NodeAgentClient(droplet_ip, self.do_token)
        
        # Step 1: Stop and remove container
        self.log(f"🗑️ Removing container {container_name} from {droplet_ip}")
        try:
            stop_result = await agent.stop_container(container_name)
            if not stop_result.success:
                self.log(f"   ⚠️ Stop failed (may already be stopped): {stop_result.error}")
            
            remove_result = await agent.remove_container(container_name)
            if remove_result.success:
                result.container_removed = True
                self.log(f"   ✅ Container removed")
            else:
                result.errors.append(f"Container removal failed: {remove_result.error}")
                self.log(f"   ❌ Container removal failed: {remove_result.error}")
        except Exception as e:
            result.errors.append(f"Container removal error: {e}")
            self.log(f"   ❌ Container removal error: {e}")
        
        # Step 2: Remove nginx config
        if cleanup_nginx:
            try:
                # Config name is typically the container name or domain
                config_name = domain or container_name
                nginx_result = await agent.remove_nginx_config(config_name, reload=True)
                if nginx_result.success:
                    result.nginx_removed = True
                    self.log(f"   ✅ Nginx config removed")
                else:
                    # Not an error if config didn't exist
                    self.log(f"   ℹ️ Nginx config not found or already removed")
            except Exception as e:
                result.errors.append(f"Nginx cleanup error: {e}")
                self.log(f"   ⚠️ Nginx cleanup error: {e}")
        
        # Step 3: Update DNS (remove this IP from A records)
        if cleanup_dns and domain and self.cf_token:
            try:
                dns_result = await self._remove_ip_from_dns(domain, droplet_ip)
                result.dns_updated = dns_result
                if dns_result:
                    self.log(f"   ✅ DNS updated (removed {droplet_ip})")
                else:
                    self.log(f"   ℹ️ DNS not updated (IP not in records or error)")
            except Exception as e:
                result.errors.append(f"DNS cleanup error: {e}")
                self.log(f"   ⚠️ DNS cleanup error: {e}")
        
        # Overall success if container was removed (main goal)
        result.success = result.container_removed
        result.details = {
            "container_name": container_name,
            "droplet_ip": droplet_ip,
            "domain": domain,
        }
        
        return result
    
    async def remove_service(
        self,
        container_name: str,
        droplet_ips: List[str],
        domain: Optional[str] = None,
        cleanup_nginx: bool = True,
        cleanup_dns: bool = True,
    ) -> UndeployResult:
        """
        Remove service from ALL droplets.
        
        Args:
            container_name: Name of container to remove
            droplet_ips: List of all droplet IPs running this service
            domain: Full domain name for DNS cleanup
            cleanup_nginx: Remove nginx configs (default True)
            cleanup_dns: Remove DNS records entirely (default True)
            
        Returns:
            UndeployResult with aggregated status
        """
        result = UndeployResult(success=True)
        containers_removed = 0
        nginx_removed = 0
        
        self.log(f"🗑️ Removing service {container_name} from {len(droplet_ips)} droplet(s)")
        
        # Remove from each droplet (don't update DNS per-droplet, do it once at end)
        for ip in droplet_ips:
            sub_result = await self.remove_container_from_droplet(
                container_name=container_name,
                droplet_ip=ip,
                domain=domain,
                cleanup_nginx=cleanup_nginx,
                cleanup_dns=False,  # We'll do DNS once at the end
            )
            
            if sub_result.container_removed:
                containers_removed += 1
            if sub_result.nginx_removed:
                nginx_removed += 1
            
            result.errors.extend(sub_result.errors)
        
        result.container_removed = containers_removed > 0
        
        # Remove DNS entirely (all IPs removed)
        if cleanup_dns and domain and self.cf_token:
            try:
                dns_result = await self._delete_dns_record(domain)
                result.dns_updated = dns_result
                if dns_result:
                    self.log(f"   ✅ DNS record deleted for {domain}")
            except Exception as e:
                result.errors.append(f"DNS deletion error: {e}")
                self.log(f"   ⚠️ DNS deletion error: {e}")
        
        result.success = containers_removed == len(droplet_ips)
        result.nginx_removed = nginx_removed > 0
        result.details = {
            "container_name": container_name,
            "droplets_total": len(droplet_ips),
            "containers_removed": containers_removed,
            "nginx_configs_removed": nginx_removed,
            "domain": domain,
        }
        
        self.log(f"   Summary: {containers_removed}/{len(droplet_ips)} containers removed")
        
        return result
    
    async def cleanup_droplet_dns(
        self,
        droplet_ip: str,
        domains: Optional[List[str]] = None,
    ) -> Dict[str, bool]:
        """
        Remove a droplet's IP from all DNS records.
        
        Useful when deleting a droplet - removes its IP from all A records.
        
        Args:
            droplet_ip: IP address being removed
            domains: Specific domains to check (if None, would need zone scan)
            
        Returns:
            Dict of {domain: was_updated}
        """
        if not self.cf_token or not domains:
            return {}
        
        results = {}
        for domain in domains:
            try:
                updated = await self._remove_ip_from_dns(domain, droplet_ip)
                results[domain] = updated
            except Exception as e:
                self.log(f"   ⚠️ Failed to update DNS for {domain}: {e}")
                results[domain] = False
        
        return results
    
    async def _remove_ip_from_dns(self, domain: str, ip_to_remove: str) -> bool:
        """
        Remove a single IP from a domain's A records.
        
        If multiple IPs exist, removes just this one.
        If this is the last IP, deletes the record entirely.
        
        Returns:
            True if DNS was updated, False otherwise
        """
        from ..providers import AsyncCloudflareClient
        
        if not self.cf_token:
            return False
        
        cf = AsyncCloudflareClient(self.cf_token)
        try:
            # Get current A records
            records = await cf.list_records(domain, record_type="A")
            current_ips = [r.content for r in records]
            
            if ip_to_remove not in current_ips:
                # IP not in DNS, nothing to do
                return False
            
            # Remove this IP from the list
            new_ips = [ip for ip in current_ips if ip != ip_to_remove]
            
            if not new_ips:
                # Last IP - delete record entirely
                await cf.delete_all_a_records(domain)
                return True
            else:
                # Replace with remaining IPs
                await cf.replace_a_records(domain, new_ips)
                return True
        finally:
            await cf.close()
    
    async def _delete_dns_record(self, domain: str) -> bool:
        """Delete all A records for a domain."""
        from ..providers import AsyncCloudflareClient
        
        if not self.cf_token:
            return False
        
        cf = AsyncCloudflareClient(self.cf_token)
        try:
            deleted = await cf.delete_all_a_records(domain)
            return deleted > 0
        finally:
            await cf.close()


class UndeployService:
    """
    Service for removing containers and services (sync).
    
    Wraps AsyncUndeployService for use in scripts and CLI.
    """
    
    def __init__(
        self,
        do_token: str,
        cf_token: Optional[str] = None,
        zone: Optional[str] = None,
        log: Optional[Callable[[str], None]] = None,
    ):
        self._async_service = AsyncUndeployService(do_token, cf_token, zone, log)
    
    def remove_container_from_droplet(
        self,
        container_name: str,
        droplet_ip: str,
        domain: Optional[str] = None,
        cleanup_nginx: bool = True,
        cleanup_dns: bool = False,
    ) -> UndeployResult:
        """Remove ONE container from ONE droplet (sync)."""
        return asyncio.run(self._async_service.remove_container_from_droplet(
            container_name=container_name,
            droplet_ip=droplet_ip,
            domain=domain,
            cleanup_nginx=cleanup_nginx,
            cleanup_dns=cleanup_dns,
        ))
    
    def remove_service(
        self,
        container_name: str,
        droplet_ips: List[str],
        domain: Optional[str] = None,
        cleanup_nginx: bool = True,
        cleanup_dns: bool = True,
    ) -> UndeployResult:
        """Remove service from ALL droplets (sync)."""
        return asyncio.run(self._async_service.remove_service(
            container_name=container_name,
            droplet_ips=droplet_ips,
            domain=domain,
            cleanup_nginx=cleanup_nginx,
            cleanup_dns=cleanup_dns,
        ))
    
    def cleanup_droplet_dns(
        self,
        droplet_ip: str,
        domains: Optional[List[str]] = None,
    ) -> Dict[str, bool]:
        """Remove a droplet's IP from DNS records (sync)."""
        return asyncio.run(self._async_service.cleanup_droplet_dns(
            droplet_ip=droplet_ip,
            domains=domains,
        ))
