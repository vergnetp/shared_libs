"""
Cloudflare Client for Infrastructure Module.

Extends shared cloud.CloudflareClient with infra-specific methods.
Provides both sync and async variants.

Usage:
    # Sync (for scripts)
    from shared_libs.backend.infra.cloud import CloudflareClient
    cf = CloudflareClient(api_token="...")
    cf.upsert_a_record(domain="api.example.com", ip="1.2.3.4")
    
    # Async (for FastAPI)
    from shared_libs.backend.infra.cloud import AsyncCloudflareClient
    cf = AsyncCloudflareClient(api_token="...")
    await cf.upsert_a_record(domain="api.example.com", ip="1.2.3.4")
"""

from __future__ import annotations
from typing import List, Dict, Any, Optional

# Import from shared cloud module (try multiple paths for flexibility)
try:
    from shared_libs.backend.cloud.cloudflare import (
        CloudflareClient as _BaseCloudflareClient,
        AsyncCloudflareClient as _BaseAsyncCloudflareClient,
        DNSRecord,
    )
    from shared_libs.backend.cloud.errors import CloudflareError
except ImportError:
    # Fallback for direct execution
    from cloud.cloudflare import (
        CloudflareClient as _BaseCloudflareClient,
        AsyncCloudflareClient as _BaseAsyncCloudflareClient,
        DNSRecord,
    )
    from cloud.errors import CloudflareError


class CloudflareClient(_BaseCloudflareClient):
    """
    Cloudflare client with infra-specific extensions (sync).
    
    Extends shared cloud.CloudflareClient with:
    - delete_all_a_records() - Remove all A records for a domain
    - replace_a_records() - Replace all A records atomically
    - upsert_cname_record() - Create or update CNAME record
    """
    
    def delete_all_a_records(self, domain: str) -> int:
        """
        Delete all A records for a domain.
        
        Args:
            domain: Full domain name (e.g., "api.example.com")
            
        Returns:
            Number of records deleted
        """
        records = self.list_records(domain, record_type="A")
        deleted = 0
        
        for record in records:
            if self.delete_record(record=record):
                deleted += 1
        
        return deleted
    
    def replace_a_records(
        self,
        domain: str,
        ips: List[str],
        proxied: bool = True,
        ttl: int = 1,
    ) -> List[DNSRecord]:
        """
        Replace all A records with new IPs (atomic replacement).
        
        Useful for updating server IPs without downtime.
        
        Args:
            domain: Full domain name
            ips: List of new IP addresses
            proxied: Enable Cloudflare proxy
            ttl: TTL in seconds (1 = auto)
            
        Returns:
            List of created DNS records
        """
        # Delete existing A records
        self.delete_all_a_records(domain)
        
        # Create new records
        created = []
        for ip in ips:
            record = self.create_record(
                domain=domain,
                record_type="A",
                content=ip,
                proxied=proxied,
                ttl=ttl,
            )
            if record:
                created.append(record)
        
        return created
    
    def upsert_cname_record(
        self,
        domain: str,
        target: str,
        proxied: bool = True,
        ttl: int = 1,
    ) -> Optional[DNSRecord]:
        """
        Create or update a CNAME record.
        
        Args:
            domain: Full domain name (e.g., "www.example.com")
            target: Target domain (e.g., "example.com")
            proxied: Enable Cloudflare proxy
            ttl: TTL in seconds (1 = auto)
            
        Returns:
            The created/updated DNS record
        """
        existing = self.get_record(domain, record_type="CNAME")
        
        if existing:
            if existing.content == target and existing.proxied == proxied:
                return existing
            return self.update_record(
                record=existing,
                content=target,
                proxied=proxied,
                ttl=ttl,
            )
        else:
            return self.create_record(
                domain=domain,
                record_type="CNAME",
                content=target,
                proxied=proxied,
                ttl=ttl,
            )


class AsyncCloudflareClient(_BaseAsyncCloudflareClient):
    """
    Cloudflare client with infra-specific extensions (async).
    
    Async variant for use in FastAPI and other async contexts.
    """
    
    async def delete_all_a_records(self, domain: str) -> int:
        """Delete all A records for a domain."""
        records = await self.list_records(domain, record_type="A")
        deleted = 0
        
        for record in records:
            if await self.delete_record(record=record):
                deleted += 1
        
        return deleted
    
    async def replace_a_records(
        self,
        domain: str,
        ips: List[str],
        proxied: bool = True,
        ttl: int = 1,
    ) -> List[DNSRecord]:
        """Replace all A records with new IPs (atomic replacement)."""
        # Delete existing A records
        await self.delete_all_a_records(domain)
        
        # Create new records
        created = []
        for ip in ips:
            record = await self.create_record(
                domain=domain,
                record_type="A",
                content=ip,
                proxied=proxied,
                ttl=ttl,
            )
            if record:
                created.append(record)
        
        return created
    
    async def upsert_cname_record(
        self,
        domain: str,
        target: str,
        proxied: bool = True,
        ttl: int = 1,
    ) -> Optional[DNSRecord]:
        """Create or update a CNAME record."""
        existing = await self.get_record(domain, record_type="CNAME")
        
        if existing:
            if existing.content == target and existing.proxied == proxied:
                return existing
            return await self.update_record(
                record=existing,
                content=target,
                proxied=proxied,
                ttl=ttl,
            )
        else:
            return await self.create_record(
                domain=domain,
                record_type="CNAME",
                content=target,
                proxied=proxied,
                ttl=ttl,
            )


# Re-export for convenience
__all__ = [
    "CloudflareClient",
    "AsyncCloudflareClient",
    "CloudflareError",
    "DNSRecord",
]
