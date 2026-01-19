"""
Cloudflare Load Balancer Service - DNS-based load balancing.

Usage (Sync):
    from infra.dns import CloudflareLBService
    service = CloudflareLBService(cf_token)
    result = service.setup_lb(domain="api.example.com", server_ips=["1.2.3.4", "5.6.7.8"])

Usage (Async):
    from infra.dns import AsyncCloudflareLBService
    service = AsyncCloudflareLBService(cf_token)
    result = await service.setup_lb(domain="api.example.com", server_ips=["1.2.3.4", "5.6.7.8"])
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


@dataclass
class LBSetupResult:
    """Result of load balancer setup."""
    success: bool
    domain: str = ""
    records_created: int = 0
    results: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "domain": self.domain,
            "records_created": self.records_created,
            "results": self.results,
            "error": self.error,
        }


class CloudflareLBService:
    """
    Cloudflare DNS-based Load Balancer Service (sync).
    
    Creates multiple A records for round-robin DNS load balancing.
    """
    
    def __init__(self, cf_token: str):
        self.cf_token = cf_token
    
    def setup_lb(
        self,
        domain: str,
        server_ips: List[str],
        proxied: bool = True,
        ttl: int = 1,
    ) -> LBSetupResult:
        """
        Setup DNS load balancer by creating A records for each server IP.
        
        Args:
            domain: Full domain name (e.g., "api.example.com")
            server_ips: List of server IP addresses
            proxied: Enable Cloudflare proxy (default True)
            ttl: TTL in seconds (1 = auto)
            
        Returns:
            LBSetupResult with created records
        """
        from ..providers import CloudflareClient
        
        cf = CloudflareClient(self.cf_token)
        results = []
        records_created = 0
        
        for ip in server_ips:
            ip = ip.strip()
            try:
                cf.create_or_update_record(
                    domain=domain,
                    record_type="A",
                    content=ip,
                    proxied=proxied,
                    ttl=ttl,
                )
                results.append({"ip": ip, "success": True})
                records_created += 1
            except Exception as e:
                results.append({"ip": ip, "success": False, "error": str(e)})
        
        all_success = all(r.get("success") for r in results)
        
        return LBSetupResult(
            success=all_success,
            domain=domain,
            records_created=records_created,
            results=results,
        )
    
    def remove_lb(self, domain: str) -> LBSetupResult:
        """
        Remove all A records for a domain (teardown LB).
        
        Args:
            domain: Full domain name
            
        Returns:
            LBSetupResult with removal info
        """
        from ..providers import CloudflareClient
        
        cf = CloudflareClient(self.cf_token)
        
        try:
            deleted = cf.delete_all_a_records(domain)
            return LBSetupResult(
                success=True,
                domain=domain,
                records_created=0,
                results=[{"deleted": deleted}],
            )
        except Exception as e:
            return LBSetupResult(
                success=False,
                domain=domain,
                error=str(e),
            )
    
    def replace_servers(
        self,
        domain: str,
        server_ips: List[str],
        proxied: bool = True,
        ttl: int = 1,
    ) -> LBSetupResult:
        """
        Atomically replace all servers in the load balancer.
        
        Args:
            domain: Full domain name
            server_ips: New list of server IPs
            proxied: Enable Cloudflare proxy
            ttl: TTL in seconds
            
        Returns:
            LBSetupResult
        """
        from ..providers import CloudflareClient
        
        cf = CloudflareClient(self.cf_token)
        
        try:
            records = cf.replace_a_records(domain, server_ips, proxied=proxied, ttl=ttl)
            return LBSetupResult(
                success=True,
                domain=domain,
                records_created=len(records),
                results=[{"ip": r.content, "success": True} for r in records],
            )
        except Exception as e:
            return LBSetupResult(
                success=False,
                domain=domain,
                error=str(e),
            )


class AsyncCloudflareLBService:
    """
    Cloudflare DNS-based Load Balancer Service (async).
    """
    
    def __init__(self, cf_token: str):
        self.cf_token = cf_token
    
    async def setup_lb(
        self,
        domain: str,
        server_ips: List[str],
        proxied: bool = True,
        ttl: int = 1,
    ) -> LBSetupResult:
        """
        Setup DNS load balancer by creating A records for each server IP (async).
        """
        from ..providers import AsyncCloudflareClient
        
        cf = AsyncCloudflareClient(self.cf_token)
        results = []
        records_created = 0
        
        try:
            for ip in server_ips:
                ip = ip.strip()
                try:
                    await cf.create_or_update_record(
                        domain=domain,
                        record_type="A",
                        content=ip,
                        proxied=proxied,
                        ttl=ttl,
                    )
                    results.append({"ip": ip, "success": True})
                    records_created += 1
                except Exception as e:
                    results.append({"ip": ip, "success": False, "error": str(e)})
        finally:
            await cf.close()
        
        all_success = all(r.get("success") for r in results)
        
        return LBSetupResult(
            success=all_success,
            domain=domain,
            records_created=records_created,
            results=results,
        )
    
    async def remove_lb(self, domain: str) -> LBSetupResult:
        """Remove all A records for a domain (async)."""
        from ..providers import AsyncCloudflareClient
        
        cf = AsyncCloudflareClient(self.cf_token)
        
        try:
            deleted = await cf.delete_all_a_records(domain)
            return LBSetupResult(
                success=True,
                domain=domain,
                records_created=0,
                results=[{"deleted": deleted}],
            )
        except Exception as e:
            return LBSetupResult(
                success=False,
                domain=domain,
                error=str(e),
            )
        finally:
            await cf.close()
    
    async def replace_servers(
        self,
        domain: str,
        server_ips: List[str],
        proxied: bool = True,
        ttl: int = 1,
    ) -> LBSetupResult:
        """Atomically replace all servers in the load balancer (async)."""
        from ..providers import AsyncCloudflareClient
        
        cf = AsyncCloudflareClient(self.cf_token)
        
        try:
            records = await cf.replace_a_records(domain, server_ips, proxied=proxied, ttl=ttl)
            return LBSetupResult(
                success=True,
                domain=domain,
                records_created=len(records),
                results=[{"ip": r.content, "success": True} for r in records],
            )
        except Exception as e:
            return LBSetupResult(
                success=False,
                domain=domain,
                error=str(e),
            )
        finally:
            await cf.close()
