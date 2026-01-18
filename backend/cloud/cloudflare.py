"""
Cloudflare Client - DNS record management.

Sync and async clients with retry, circuit breaker, and tracing.

Handles:
- Creating/updating A records
- Proxied vs direct mode
- Zone lookup
- DNS record cleanup
- Multi-server setup (FREE load balancing via multiple A records)

Usage:
    # Sync
    from cloud import CloudflareClient
    
    cf = CloudflareClient(api_token="...")
    cf.upsert_a_record(domain="api.example.com", ip="1.2.3.4")
    
    # Async
    from cloud import AsyncCloudflareClient
    
    async with AsyncCloudflareClient(api_token="...") as cf:
        await cf.upsert_a_record(domain="api.example.com", ip="1.2.3.4")
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

from .base import BaseCloudClient, AsyncBaseCloudClient, CloudClientConfig
from .errors import CloudflareError


@dataclass
class DNSRecord:
    """Cloudflare DNS record."""
    id: str
    name: str           # e.g., "api.example.com"
    type: str           # "A", "AAAA", "CNAME", etc.
    content: str        # IP address or target
    proxied: bool       # Cloudflare proxy enabled
    ttl: int            # TTL in seconds (1 = auto)
    zone_id: str
    
    @classmethod
    def from_api(cls, data: Dict[str, Any], zone_id: str) -> 'DNSRecord':
        return cls(
            id=data["id"],
            name=data["name"],
            type=data["type"],
            content=data["content"],
            proxied=data.get("proxied", False),
            ttl=data.get("ttl", 1),
            zone_id=zone_id,
        )


# =============================================================================
# Sync Client
# =============================================================================

class CloudflareClient(BaseCloudClient):
    """
    Cloudflare API client for DNS management (sync).
    
    Usage:
        cf = CloudflareClient(api_token="...")
        
        # Create or update A record (proxied through Cloudflare)
        cf.upsert_a_record(
            domain="api.example.com",
            ip="1.2.3.4",
            proxied=True,  # Cloudflare handles SSL
        )
        
        # Delete record
        cf.delete_record(domain="api.example.com")
        
        # List records
        records = cf.list_records(zone="example.com")
    
    Proxied mode (recommended):
        - Cloudflare handles SSL certificates
        - DDoS protection
        - CDN/caching for static assets
        - Hides origin IP
        
    Direct mode:
        - Direct connection to origin
        - Need own SSL certificate
        - Origin IP exposed
    """
    
    PROVIDER = "Cloudflare"
    BASE_URL = "https://api.cloudflare.com/client/v4"
    
    def __init__(
        self,
        api_token: str,
        config: CloudClientConfig = None,
    ):
        super().__init__(api_token, config)
        self._zone_cache: Dict[str, str] = {}  # domain -> zone_id
    
    # =========================================================================
    # HTTP Helpers
    # =========================================================================
    
    def _request(
        self,
        method: str,
        path: str,
        data: Dict = None,
        params: Dict = None,
    ) -> Dict[str, Any]:
        """Make API request."""
        response = self._client.request(
            method=method,
            url=path,
            json=data,
            params=params,
            raise_on_error=False,
        )
        
        result = response.json() if response.body else {}
        
        if not result.get("success", False):
            errors = result.get("errors", [])
            msg = errors[0].get("message", "Unknown error") if errors else "Unknown error"
            raise CloudflareError(msg, errors, response.status_code)
        
        return result
    
    # =========================================================================
    # Zone Management
    # =========================================================================
    
    def get_zone_id(self, domain: str) -> str:
        """
        Get zone ID for a domain.
        
        Extracts the registrable domain (e.g., api.example.com -> example.com)
        and looks up its zone ID.
        """
        parts = domain.rstrip(".").split(".")
        if len(parts) >= 2:
            if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "gov"):
                zone_name = ".".join(parts[-3:])
            else:
                zone_name = ".".join(parts[-2:])
        else:
            zone_name = domain
        
        if zone_name in self._zone_cache:
            return self._zone_cache[zone_name]
        
        result = self._request("GET", "/zones", params={"name": zone_name})
        zones = result.get("result", [])
        
        if not zones:
            raise CloudflareError(f"Zone not found for domain: {domain} (looked for {zone_name})")
        
        zone_id = zones[0]["id"]
        self._zone_cache[zone_name] = zone_id
        
        return zone_id
    
    def list_zones(self) -> List[Dict[str, Any]]:
        """List all zones in account."""
        result = self._request("GET", "/zones")
        return result.get("result", [])
    
    # =========================================================================
    # DNS Record Management
    # =========================================================================
    
    def list_records(
        self,
        zone: str = None,
        zone_id: str = None,
        record_type: str = None,
        name: str = None,
    ) -> List[DNSRecord]:
        """
        List DNS records.
        
        Args:
            zone: Zone name (e.g., "example.com")
            zone_id: Zone ID (alternative to zone name)
            record_type: Filter by type (A, AAAA, CNAME, etc.)
            name: Filter by record name
        """
        if not zone_id:
            zone_id = self.get_zone_id(zone)
        
        params = {}
        if record_type:
            params["type"] = record_type
        if name:
            params["name"] = name
        
        result = self._request("GET", f"/zones/{zone_id}/dns_records", params=params)
        
        return [
            DNSRecord.from_api(r, zone_id)
            for r in result.get("result", [])
        ]
    
    def get_record(self, domain: str, record_type: str = "A") -> Optional[DNSRecord]:
        """Get a specific DNS record."""
        zone_id = self.get_zone_id(domain)
        records = self.list_records(zone_id=zone_id, record_type=record_type, name=domain)
        return records[0] if records else None
    
    def create_record(
        self,
        domain: str,
        record_type: str,
        content: str,
        proxied: bool = True,
        ttl: int = 1,
    ) -> DNSRecord:
        """Create a DNS record."""
        zone_id = self.get_zone_id(domain)
        
        data = {
            "type": record_type,
            "name": domain,
            "content": content,
            "proxied": proxied,
            "ttl": ttl,
        }
        
        result = self._request("POST", f"/zones/{zone_id}/dns_records", data=data)
        return DNSRecord.from_api(result.get("result", {}), zone_id)
    
    def update_record(
        self,
        record: DNSRecord = None,
        record_id: str = None,
        zone_id: str = None,
        domain: str = None,
        record_type: str = None,
        content: str = None,
        proxied: bool = None,
        ttl: int = None,
    ) -> DNSRecord:
        """Update a DNS record."""
        if record:
            record_id = record.id
            zone_id = record.zone_id
            domain = domain or record.name
            record_type = record_type or record.type
            content = content if content is not None else record.content
            proxied = proxied if proxied is not None else record.proxied
            ttl = ttl if ttl is not None else record.ttl
        
        if not zone_id:
            zone_id = self.get_zone_id(domain)
        
        data = {
            "type": record_type,
            "name": domain,
            "content": content,
            "proxied": proxied,
            "ttl": ttl,
        }
        
        result = self._request("PUT", f"/zones/{zone_id}/dns_records/{record_id}", data=data)
        return DNSRecord.from_api(result.get("result", {}), zone_id)
    
    def delete_record(
        self,
        record: DNSRecord = None,
        domain: str = None,
        record_type: str = "A",
    ) -> bool:
        """Delete a DNS record."""
        if record:
            zone_id = record.zone_id
            record_id = record.id
        else:
            zone_id = self.get_zone_id(domain)
            existing = self.list_records(zone_id=zone_id, record_type=record_type, name=domain)
            if not existing:
                return False
            record_id = existing[0].id
        
        self._request("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")
        return True
    
    def upsert_a_record(
        self,
        domain: str,
        ip: str,
        proxied: bool = True,
        ttl: int = 1,
    ) -> DNSRecord:
        """Create or update an A record."""
        existing = self.get_record(domain, "A")
        
        if existing:
            if existing.content == ip and existing.proxied == proxied:
                return existing
            return self.update_record(
                record=existing,
                content=ip,
                proxied=proxied,
                ttl=ttl,
            )
        
        return self.create_record(domain, "A", ip, proxied=proxied, ttl=ttl)
    
    # =========================================================================
    # Convenience Methods
    # =========================================================================
    
    def setup_domain(
        self,
        domain: str,
        server_ip: str,
        proxied: bool = True,
    ) -> DNSRecord:
        """
        Set up a domain to point to a server.
        
        With proxied=True (default):
        - Cloudflare handles SSL automatically
        - No need for Let's Encrypt
        - DDoS protection included
        """
        return self.upsert_a_record(domain, server_ip, proxied=proxied)
    
    def remove_domain(self, domain: str) -> bool:
        """Remove a domain's DNS record."""
        return self.delete_record(domain=domain)
    
    # =========================================================================
    # Multi-Server Setup (FREE - uses multiple A records)
    # =========================================================================
    
    def setup_multi_server(
        self,
        domain: str,
        server_ips: List[str],
        proxied: bool = True,
    ) -> List[DNSRecord]:
        """
        Set up a domain to point to multiple servers (FREE load balancing).
        
        Creates multiple A records for the same domain.
        When proxied=True, Cloudflare automatically round-robins between them.
        """
        zone_id = self.get_zone_id(domain)
        existing = self.list_records(zone_id=zone_id, record_type="A", name=domain)
        existing_ips = {r.content for r in existing}
        
        records = []
        
        for ip in server_ips:
            if ip not in existing_ips:
                record = self.create_record(domain, "A", ip, proxied=proxied)
                records.append(record)
            else:
                record = next(r for r in existing if r.content == ip)
                records.append(record)
        
        for record in existing:
            if record.content not in server_ips:
                self.delete_record(record=record)
        
        return records
    
    def add_server(
        self,
        domain: str,
        server_ip: str,
        proxied: bool = True,
    ) -> DNSRecord:
        """Add a server to existing domain (creates additional A record)."""
        zone_id = self.get_zone_id(domain)
        existing = self.list_records(zone_id=zone_id, record_type="A", name=domain)
        
        for record in existing:
            if record.content == server_ip:
                return record
        
        return self.create_record(domain, "A", server_ip, proxied=proxied)
    
    def remove_server(self, domain: str, server_ip: str) -> bool:
        """Remove a server from domain (deletes its A record)."""
        zone_id = self.get_zone_id(domain)
        existing = self.list_records(zone_id=zone_id, record_type="A", name=domain)
        
        for record in existing:
            if record.content == server_ip:
                self.delete_record(record=record)
                return True
        
        return False
    
    def list_servers(self, domain: str) -> List[str]:
        """List all server IPs for a domain."""
        zone_id = self.get_zone_id(domain)
        records = self.list_records(zone_id=zone_id, record_type="A", name=domain)
        return [r.content for r in records]
    
    # =========================================================================
    # DNS Cleanup
    # =========================================================================
    
    def cleanup_orphaned_records(
        self,
        zone: str,
        active_ips: set,
        log_fn: callable = None,
    ) -> Dict[str, Any]:
        """
        Remove DNS A records pointing to IPs that no longer exist.
        
        IMPORTANT: Only removes PROXIED records (orange cloud in Cloudflare).
        DNS-only records (gray cloud) are left untouched.
        """
        log = log_fn or (lambda x: None)
        
        try:
            zone_id = self.get_zone_id(zone)
            records = self.list_records(zone_id=zone_id, record_type="A")
            
            deleted = []
            errors = []
            kept = 0
            skipped_dns_only = 0
            
            for record in records:
                ip = record.content
                
                if not record.proxied:
                    skipped_dns_only += 1
                    continue
                
                if ip not in active_ips:
                    try:
                        self._request("DELETE", f"/zones/{zone_id}/dns_records/{record.id}")
                        deleted.append({"name": record.name, "ip": ip})
                        log(f"🧹 Removed orphaned DNS: {record.name} → {ip}")
                    except Exception as e:
                        errors.append({"name": record.name, "ip": ip, "error": str(e)})
                        log(f"⚠️ Failed to delete DNS {record.name}: {e}")
                else:
                    kept += 1
            
            if deleted:
                log(f"🧹 DNS cleanup: {len(deleted)} orphaned record(s) removed, {kept} kept, {skipped_dns_only} DNS-only skipped")
            
            return {
                "deleted": deleted,
                "kept": kept,
                "skipped_dns_only": skipped_dns_only,
                "errors": errors,
            }
            
        except CloudflareError as e:
            log(f"⚠️ DNS cleanup failed: {e.message}")
            return {"deleted": [], "kept": 0, "skipped_dns_only": 0, "errors": [{"error": e.message}]}
        except Exception as e:
            log(f"⚠️ DNS cleanup failed: {e}")
            return {"deleted": [], "kept": 0, "skipped_dns_only": 0, "errors": [{"error": str(e)}]}
    
    # Raw API methods
    def list_dns_records(self, zone_id: str) -> List[Dict[str, Any]]:
        """List all DNS records in a zone (raw API response)."""
        result = self._request("GET", f"/zones/{zone_id}/dns_records")
        return result.get("result", [])
    
    def delete_dns_record(self, zone_id: str, record_id: str) -> bool:
        """Delete a DNS record by ID."""
        self._request("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")
        return True


# =============================================================================
# Async Client
# =============================================================================

class AsyncCloudflareClient(AsyncBaseCloudClient):
    """
    Cloudflare API client for DNS management (async).
    
    Usage:
        async with AsyncCloudflareClient(api_token="...") as cf:
            await cf.upsert_a_record(domain="api.example.com", ip="1.2.3.4")
    """
    
    PROVIDER = "Cloudflare"
    BASE_URL = "https://api.cloudflare.com/client/v4"
    
    def __init__(
        self,
        api_token: str,
        config: CloudClientConfig = None,
    ):
        super().__init__(api_token, config)
        self._zone_cache: Dict[str, str] = {}
    
    # =========================================================================
    # HTTP Helpers
    # =========================================================================
    
    async def _request(
        self,
        method: str,
        path: str,
        data: Dict = None,
        params: Dict = None,
    ) -> Dict[str, Any]:
        """Make API request."""
        # Ensure cached client is initialized (lazy init for async)
        client = await self._ensure_client()
        
        response = await client.request(
            method=method,
            url=path,
            json=data,
            params=params,
            raise_on_error=False,
        )
        
        result = response.json() if response.body else {}
        
        if not result.get("success", False):
            errors = result.get("errors", [])
            msg = errors[0].get("message", "Unknown error") if errors else "Unknown error"
            raise CloudflareError(msg, errors, response.status_code)
        
        return result
    
    # =========================================================================
    # Zone Management
    # =========================================================================
    
    async def get_zone_id(self, domain: str) -> str:
        """Get zone ID for a domain."""
        parts = domain.rstrip(".").split(".")
        if len(parts) >= 2:
            if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "gov"):
                zone_name = ".".join(parts[-3:])
            else:
                zone_name = ".".join(parts[-2:])
        else:
            zone_name = domain
        
        if zone_name in self._zone_cache:
            return self._zone_cache[zone_name]
        
        result = await self._request("GET", "/zones", params={"name": zone_name})
        zones = result.get("result", [])
        
        if not zones:
            raise CloudflareError(f"Zone not found for domain: {domain} (looked for {zone_name})")
        
        zone_id = zones[0]["id"]
        self._zone_cache[zone_name] = zone_id
        
        return zone_id
    
    async def list_zones(self) -> List[Dict[str, Any]]:
        """List all zones in account."""
        result = await self._request("GET", "/zones")
        return result.get("result", [])
    
    # =========================================================================
    # DNS Record Management
    # =========================================================================
    
    async def list_records(
        self,
        zone: str = None,
        zone_id: str = None,
        record_type: str = None,
        name: str = None,
    ) -> List[DNSRecord]:
        """List DNS records."""
        if not zone_id:
            zone_id = await self.get_zone_id(zone)
        
        params = {}
        if record_type:
            params["type"] = record_type
        if name:
            params["name"] = name
        
        result = await self._request("GET", f"/zones/{zone_id}/dns_records", params=params)
        
        return [
            DNSRecord.from_api(r, zone_id)
            for r in result.get("result", [])
        ]
    
    async def get_record(self, domain: str, record_type: str = "A") -> Optional[DNSRecord]:
        """Get a specific DNS record."""
        zone_id = await self.get_zone_id(domain)
        records = await self.list_records(zone_id=zone_id, record_type=record_type, name=domain)
        return records[0] if records else None
    
    async def create_record(
        self,
        domain: str,
        record_type: str,
        content: str,
        proxied: bool = True,
        ttl: int = 1,
    ) -> DNSRecord:
        """Create a DNS record."""
        zone_id = await self.get_zone_id(domain)
        
        data = {
            "type": record_type,
            "name": domain,
            "content": content,
            "proxied": proxied,
            "ttl": ttl,
        }
        
        result = await self._request("POST", f"/zones/{zone_id}/dns_records", data=data)
        return DNSRecord.from_api(result.get("result", {}), zone_id)
    
    async def update_record(
        self,
        record: DNSRecord = None,
        record_id: str = None,
        zone_id: str = None,
        domain: str = None,
        record_type: str = None,
        content: str = None,
        proxied: bool = None,
        ttl: int = None,
    ) -> DNSRecord:
        """Update a DNS record."""
        if record:
            record_id = record.id
            zone_id = record.zone_id
            domain = domain or record.name
            record_type = record_type or record.type
            content = content if content is not None else record.content
            proxied = proxied if proxied is not None else record.proxied
            ttl = ttl if ttl is not None else record.ttl
        
        if not zone_id:
            zone_id = await self.get_zone_id(domain)
        
        data = {
            "type": record_type,
            "name": domain,
            "content": content,
            "proxied": proxied,
            "ttl": ttl,
        }
        
        result = await self._request("PUT", f"/zones/{zone_id}/dns_records/{record_id}", data=data)
        return DNSRecord.from_api(result.get("result", {}), zone_id)
    
    async def delete_record(
        self,
        record: DNSRecord = None,
        domain: str = None,
        record_type: str = "A",
    ) -> bool:
        """Delete a DNS record."""
        if record:
            zone_id = record.zone_id
            record_id = record.id
        else:
            zone_id = await self.get_zone_id(domain)
            existing = await self.list_records(zone_id=zone_id, record_type=record_type, name=domain)
            if not existing:
                return False
            record_id = existing[0].id
        
        await self._request("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")
        return True
    
    async def upsert_a_record(
        self,
        domain: str,
        ip: str,
        proxied: bool = True,
        ttl: int = 1,
    ) -> DNSRecord:
        """Create or update an A record."""
        existing = await self.get_record(domain, "A")
        
        if existing:
            if existing.content == ip and existing.proxied == proxied:
                return existing
            return await self.update_record(
                record=existing,
                content=ip,
                proxied=proxied,
                ttl=ttl,
            )
        
        return await self.create_record(domain, "A", ip, proxied=proxied, ttl=ttl)
    
    # =========================================================================
    # Convenience Methods
    # =========================================================================
    
    async def setup_domain(
        self,
        domain: str,
        server_ip: str,
        proxied: bool = True,
    ) -> DNSRecord:
        """Set up a domain to point to a server."""
        return await self.upsert_a_record(domain, server_ip, proxied=proxied)
    
    async def remove_domain(self, domain: str) -> bool:
        """Remove a domain's DNS record."""
        return await self.delete_record(domain=domain)
    
    # =========================================================================
    # Multi-Server Setup
    # =========================================================================
    
    async def setup_multi_server(
        self,
        domain: str,
        server_ips: List[str],
        proxied: bool = True,
    ) -> List[DNSRecord]:
        """Set up a domain to point to multiple servers."""
        zone_id = await self.get_zone_id(domain)
        existing = await self.list_records(zone_id=zone_id, record_type="A", name=domain)
        existing_ips = {r.content for r in existing}
        
        records = []
        
        for ip in server_ips:
            if ip not in existing_ips:
                record = await self.create_record(domain, "A", ip, proxied=proxied)
                records.append(record)
            else:
                record = next(r for r in existing if r.content == ip)
                records.append(record)
        
        for record in existing:
            if record.content not in server_ips:
                await self.delete_record(record=record)
        
        return records
    
    async def add_server(
        self,
        domain: str,
        server_ip: str,
        proxied: bool = True,
    ) -> DNSRecord:
        """Add a server to existing domain."""
        zone_id = await self.get_zone_id(domain)
        existing = await self.list_records(zone_id=zone_id, record_type="A", name=domain)
        
        for record in existing:
            if record.content == server_ip:
                return record
        
        return await self.create_record(domain, "A", server_ip, proxied=proxied)
    
    async def remove_server(self, domain: str, server_ip: str) -> bool:
        """Remove a server from domain."""
        zone_id = await self.get_zone_id(domain)
        existing = await self.list_records(zone_id=zone_id, record_type="A", name=domain)
        
        for record in existing:
            if record.content == server_ip:
                await self.delete_record(record=record)
                return True
        
        return False
    
    async def list_servers(self, domain: str) -> List[str]:
        """List all server IPs for a domain."""
        zone_id = await self.get_zone_id(domain)
        records = await self.list_records(zone_id=zone_id, record_type="A", name=domain)
        return [r.content for r in records]
