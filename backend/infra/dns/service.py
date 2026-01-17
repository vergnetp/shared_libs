"""
DNS Cleanup Service - Clean up orphaned DNS records.

Usage (Sync):
    from infra.dns import DnsCleanupService
    result = DnsCleanupService(do_token, cf_token).cleanup_orphaned(zone_name, dry_run=True)

Usage (Async):
    from infra.dns import AsyncDnsCleanupService
    result = await AsyncDnsCleanupService(do_token, cf_token).cleanup_orphaned(zone_name)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Set


@dataclass
class DnsCleanupResult:
    """Result of DNS cleanup operation."""
    success: bool
    dry_run: bool = False
    zone: str = ""
    active_droplet_ips: List[str] = field(default_factory=list)
    kept_records: int = 0
    skipped_dns_only: int = 0
    orphaned_records: int = 0
    deleted: List[Dict[str, Any]] = field(default_factory=list)
    failed: List[Dict[str, Any]] = field(default_factory=list)
    message: str = ""
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "dry_run": self.dry_run,
            "zone": self.zone,
            "active_droplet_ips": self.active_droplet_ips,
            "kept_records": self.kept_records,
            "skipped_dns_only": self.skipped_dns_only,
            "orphaned_records": self.orphaned_records,
            "deleted": self.deleted,
            "failed": self.failed,
            "message": self.message,
            "error": self.error,
        }


class _BaseDnsCleanupService:
    """Base class with shared DNS cleanup logic."""
    
    def __init__(self, do_token: str, cf_token: str):
        self.do_token = do_token
        self.cf_token = cf_token
    
    def _find_orphaned_records(
        self,
        records: List[Dict[str, Any]],
        active_ips: Set[str],
    ) -> tuple:
        """Find orphaned DNS records."""
        orphaned = []
        kept = []
        skipped_dns_only = []
        
        for record in records:
            if record.get("type") != "A":
                continue
            
            ip = record.get("content")
            proxied = record.get("proxied", False)
            record_info = {
                "id": record.get("id"),
                "name": record.get("name"),
                "ip": ip,
                "proxied": proxied,
            }
            
            # Skip DNS-only records (not proxied) - manual entries
            if not proxied:
                skipped_dns_only.append(record_info)
                continue
            
            if ip not in active_ips:
                orphaned.append(record_info)
            else:
                kept.append(record_info)
        
        return orphaned, kept, skipped_dns_only


class DnsCleanupService(_BaseDnsCleanupService):
    """Synchronous DNS cleanup service."""
    
    def cleanup_orphaned(
        self,
        zone_name: str,
        dry_run: bool = False,
    ) -> DnsCleanupResult:
        """Clean up orphaned DNS records (sync)."""
        from ..cloud import DOClient, CloudflareClient
        
        try:
            # Get active droplet IPs
            do_client = DOClient(self.do_token)
            droplets = do_client.list_droplets()
            active_ips = set()
            for d in droplets:
                if d.ip:
                    active_ips.add(d.ip)
            
            # Get DNS records
            cf = CloudflareClient(self.cf_token)
            zones = cf.list_zones()
            zone = next((z for z in zones if z.get("name") == zone_name), None)
            if not zone:
                return DnsCleanupResult(
                    success=False,
                    error=f"Zone '{zone_name}' not found"
                )
            
            zone_id = zone["id"]
            records = cf.list_dns_records(zone_id)
            
            # Find orphaned records
            orphaned, kept, skipped_dns_only = self._find_orphaned_records(records, active_ips)
            
            # Delete if not dry run
            deleted = []
            failed = []
            if not dry_run:
                for record in orphaned:
                    try:
                        cf.delete_dns_record(zone_id, record["id"])
                        deleted.append(record)
                    except Exception as e:
                        record["error"] = str(e)
                        failed.append(record)
            
            return DnsCleanupResult(
                success=True,
                dry_run=dry_run,
                zone=zone_name,
                active_droplet_ips=list(active_ips),
                kept_records=len(kept),
                skipped_dns_only=len(skipped_dns_only),
                orphaned_records=len(orphaned),
                deleted=deleted if not dry_run else orphaned,
                failed=failed,
                message=f"{'Would delete' if dry_run else 'Deleted'} {len(orphaned)} orphaned record(s)"
            )
            
        except Exception as e:
            return DnsCleanupResult(success=False, error=str(e))


class AsyncDnsCleanupService(_BaseDnsCleanupService):
    """Asynchronous DNS cleanup service."""
    
    async def cleanup_orphaned(
        self,
        zone_name: str,
        dry_run: bool = False,
    ) -> DnsCleanupResult:
        """Clean up orphaned DNS records (async)."""
        from ..cloud import AsyncDOClient, AsyncCloudflareClient
        
        try:
            # Get active droplet IPs
            do_client = AsyncDOClient(self.do_token)
            try:
                droplets = await do_client.list_droplets()
                active_ips = set()
                for d in droplets:
                    if d.ip:
                        active_ips.add(d.ip)
            finally:
                await do_client.close()
            
            # Get DNS records
            cf = AsyncCloudflareClient(self.cf_token)
            try:
                zones = await cf.list_zones()
                zone = next((z for z in zones if z.get("name") == zone_name), None)
                if not zone:
                    return DnsCleanupResult(
                        success=False,
                        error=f"Zone '{zone_name}' not found"
                    )
                
                zone_id = zone["id"]
                records = await cf.list_dns_records(zone_id)
                
                # Find orphaned records
                orphaned, kept, skipped_dns_only = self._find_orphaned_records(records, active_ips)
                
                # Delete if not dry run
                deleted = []
                failed = []
                if not dry_run:
                    for record in orphaned:
                        try:
                            await cf.delete_dns_record(zone_id, record["id"])
                            deleted.append(record)
                        except Exception as e:
                            record["error"] = str(e)
                            failed.append(record)
            finally:
                await cf.close()
            
            return DnsCleanupResult(
                success=True,
                dry_run=dry_run,
                zone=zone_name,
                active_droplet_ips=list(active_ips),
                kept_records=len(kept),
                skipped_dns_only=len(skipped_dns_only),
                orphaned_records=len(orphaned),
                deleted=deleted if not dry_run else orphaned,
                failed=failed,
                message=f"{'Would delete' if dry_run else 'Deleted'} {len(orphaned)} orphaned record(s)"
            )
            
        except Exception as e:
            return DnsCleanupResult(success=False, error=str(e))
