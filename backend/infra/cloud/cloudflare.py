"""
Cloudflare Client - DNS record management.

Handles:
- Creating/updating A records
- Proxied vs direct mode
- Zone lookup
- DNS record cleanup
"""

import requests
from dataclasses import dataclass
from typing import Optional, List, Dict, Any


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


class CloudflareError(Exception):
    """Cloudflare API error."""
    def __init__(self, message: str, errors: List[Dict] = None):
        self.message = message
        self.errors = errors or []
        super().__init__(message)


class CloudflareClient:
    """
    Cloudflare API client for DNS management.
    
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
    
    BASE_URL = "https://api.cloudflare.com/client/v4"
    
    def __init__(self, api_token: str):
        """
        Initialize client with API token.
        
        Args:
            api_token: Cloudflare API token with DNS edit permissions
        """
        self.api_token = api_token
        self._zone_cache: Dict[str, str] = {}  # domain -> zone_id
    
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }
    
    def _request(
        self,
        method: str,
        path: str,
        data: Dict = None,
        params: Dict = None,
    ) -> Dict[str, Any]:
        """Make API request."""
        url = f"{self.BASE_URL}{path}"
        
        response = requests.request(
            method=method,
            url=url,
            headers=self._headers(),
            json=data,
            params=params,
            timeout=30,
        )
        
        result = response.json()
        
        if not result.get("success", False):
            errors = result.get("errors", [])
            msg = errors[0].get("message", "Unknown error") if errors else "Unknown error"
            raise CloudflareError(msg, errors)
        
        return result
    
    # =========================================================================
    # Zone Management
    # =========================================================================
    
    def get_zone_id(self, domain: str) -> str:
        """
        Get zone ID for a domain.
        
        Extracts the registrable domain (e.g., api.example.com -> example.com)
        and looks up its zone ID.
        
        Args:
            domain: Full domain name
            
        Returns:
            Zone ID string
            
        Raises:
            CloudflareError: If zone not found
        """
        # Extract base domain (simple approach: last two parts)
        parts = domain.rstrip(".").split(".")
        if len(parts) >= 2:
            # Handle common TLDs like .co.uk
            if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "gov"):
                zone_name = ".".join(parts[-3:])
            else:
                zone_name = ".".join(parts[-2:])
        else:
            zone_name = domain
        
        # Check cache
        if zone_name in self._zone_cache:
            return self._zone_cache[zone_name]
        
        # Look up zone
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
            
        Returns:
            List of DNSRecord objects
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
        """
        Get a specific DNS record.
        
        Args:
            domain: Full domain name
            record_type: Record type (default: A)
            
        Returns:
            DNSRecord or None if not found
        """
        zone_id = self.get_zone_id(domain)
        records = self.list_records(zone_id=zone_id, record_type=record_type, name=domain)
        return records[0] if records else None
    
    def create_record(
        self,
        domain: str,
        record_type: str,
        content: str,
        proxied: bool = True,
        ttl: int = 1,  # 1 = auto
    ) -> DNSRecord:
        """
        Create a DNS record.
        
        Args:
            domain: Full domain name
            record_type: Record type (A, AAAA, CNAME, etc.)
            content: Record content (IP or target)
            proxied: Enable Cloudflare proxy
            ttl: TTL in seconds (1 = auto)
            
        Returns:
            Created DNSRecord
        """
        zone_id = self.get_zone_id(domain)
        
        data = {
            "type": record_type,
            "name": domain,
            "content": content,
            "proxied": proxied,
            "ttl": ttl,
        }
        
        result = self._request("POST", f"/zones/{zone_id}/dns_records", data=data)
        return DNSRecord.from_api(result["result"], zone_id)
    
    def update_record(
        self,
        record: DNSRecord,
        content: str = None,
        proxied: bool = None,
        ttl: int = None,
    ) -> DNSRecord:
        """
        Update an existing DNS record.
        
        Args:
            record: Existing DNSRecord to update
            content: New content (optional)
            proxied: New proxied setting (optional)
            ttl: New TTL (optional)
            
        Returns:
            Updated DNSRecord
        """
        data = {
            "type": record.type,
            "name": record.name,
            "content": content or record.content,
            "proxied": proxied if proxied is not None else record.proxied,
            "ttl": ttl or record.ttl,
        }
        
        result = self._request(
            "PUT",
            f"/zones/{record.zone_id}/dns_records/{record.id}",
            data=data,
        )
        return DNSRecord.from_api(result["result"], record.zone_id)
    
    def delete_record(self, domain: str = None, record: DNSRecord = None) -> bool:
        """
        Delete a DNS record.
        
        Args:
            domain: Domain name (looks up A record)
            record: DNSRecord object (alternative to domain)
            
        Returns:
            True if deleted
        """
        if not record and domain:
            record = self.get_record(domain)
        
        if not record:
            return False
        
        self._request("DELETE", f"/zones/{record.zone_id}/dns_records/{record.id}")
        return True
    
    def delete_all_a_records(self, domain: str) -> int:
        """
        Delete ALL A records for a domain.
        
        Args:
            domain: Full domain name
            
        Returns:
            Number of records deleted
        """
        zone_id = self.get_zone_id(domain)
        records = self.list_records(zone_id=zone_id, record_type="A", name=domain)
        
        for record in records:
            self._request("DELETE", f"/zones/{zone_id}/dns_records/{record.id}")
        
        return len(records)
    
    def replace_a_records(
        self,
        domain: str,
        ips: List[str],
        proxied: bool = True,
        ttl: int = 1,
    ) -> List[DNSRecord]:
        """
        Replace ALL A records for a domain with new IPs.
        
        Deletes all existing A records, then creates new ones.
        Use this for multi-server deployments to ensure clean state.
        
        Args:
            domain: Full domain name
            ips: List of IPv4 addresses
            proxied: Enable Cloudflare proxy
            ttl: TTL in seconds (1 = auto)
            
        Returns:
            List of created DNSRecords
        """
        # Delete all existing A records
        deleted = self.delete_all_a_records(domain)
        
        # Create new records for each IP
        created = []
        for ip in ips:
            record = self.create_record(domain, "A", ip, proxied=proxied, ttl=ttl)
            created.append(record)
        
        return created
    
    # =========================================================================
    # Convenience Methods
    # =========================================================================
    
    def upsert_a_record(
        self,
        domain: str,
        ip: str,
        proxied: bool = True,
        ttl: int = 1,
    ) -> DNSRecord:
        """
        Create or update an A record.
        
        Args:
            domain: Full domain name (e.g., "api.example.com")
            ip: IPv4 address
            proxied: Enable Cloudflare proxy (recommended)
            ttl: TTL in seconds (1 = auto)
            
        Returns:
            DNSRecord (created or updated)
        """
        existing = self.get_record(domain, record_type="A")
        
        if existing:
            if existing.content == ip and existing.proxied == proxied:
                return existing  # No change needed
            return self.update_record(existing, content=ip, proxied=proxied, ttl=ttl)
        else:
            return self.create_record(domain, "A", ip, proxied=proxied, ttl=ttl)
    
    def upsert_cname_record(
        self,
        domain: str,
        target: str,
        proxied: bool = True,
        ttl: int = 1,
    ) -> DNSRecord:
        """
        Create or update a CNAME record.
        
        Args:
            domain: Full domain name
            target: Target domain
            proxied: Enable Cloudflare proxy
            ttl: TTL in seconds
            
        Returns:
            DNSRecord (created or updated)
        """
        existing = self.get_record(domain, record_type="CNAME")
        
        if existing:
            if existing.content == target and existing.proxied == proxied:
                return existing
            return self.update_record(existing, content=target, proxied=proxied, ttl=ttl)
        else:
            return self.create_record(domain, "CNAME", target, proxied=proxied, ttl=ttl)
    
    def setup_domain(
        self,
        domain: str,
        server_ip: str,
        proxied: bool = True,
    ) -> DNSRecord:
        """
        Set up a domain to point to a server.
        
        This is the main method to call when deploying a service.
        Creates/updates an A record pointing to the server IP.
        
        With proxied=True (default):
        - Cloudflare handles SSL automatically
        - No need for Let's Encrypt
        - DDoS protection included
        
        Args:
            domain: Full domain name
            server_ip: Server IP address
            proxied: Use Cloudflare proxy (strongly recommended)
            
        Returns:
            DNSRecord
        """
        return self.upsert_a_record(domain, server_ip, proxied=proxied)
    
    def remove_domain(self, domain: str) -> bool:
        """
        Remove a domain's DNS record.
        
        Call this when decommissioning a service.
        
        Args:
            domain: Full domain name
            
        Returns:
            True if deleted, False if not found
        """
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
        
        This is FREE and doesn't require Cloudflare's paid Load Balancer feature.
        
        Args:
            domain: Full domain name (e.g., "api.example.com")
            server_ips: List of server IP addresses
            proxied: Use Cloudflare proxy (strongly recommended)
            
        Returns:
            List of created DNSRecord objects
        """
        zone_id = self.get_zone_id(domain)
        
        # Get existing A records for this domain
        existing = self.list_records(zone_id=zone_id, record_type="A", name=domain)
        existing_ips = {r.content for r in existing}
        
        records = []
        
        # Add new IPs
        for ip in server_ips:
            if ip not in existing_ips:
                record = self.create_record(domain, "A", ip, proxied=proxied)
                records.append(record)
            else:
                # Already exists
                record = next(r for r in existing if r.content == ip)
                records.append(record)
        
        # Remove IPs no longer in list
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
        """
        Add a server to existing domain (creates additional A record).
        
        Args:
            domain: Full domain name
            server_ip: Server IP to add
            proxied: Use Cloudflare proxy
            
        Returns:
            Created DNSRecord
        """
        # Check if already exists
        zone_id = self.get_zone_id(domain)
        existing = self.list_records(zone_id=zone_id, record_type="A", name=domain)
        
        for record in existing:
            if record.content == server_ip:
                return record  # Already exists
        
        return self.create_record(domain, "A", server_ip, proxied=proxied)
    
    def remove_server(
        self,
        domain: str,
        server_ip: str,
    ) -> bool:
        """
        Remove a server from domain (deletes its A record).
        
        Args:
            domain: Full domain name
            server_ip: Server IP to remove
            
        Returns:
            True if deleted, False if not found
        """
        zone_id = self.get_zone_id(domain)
        existing = self.list_records(zone_id=zone_id, record_type="A", name=domain)
        
        for record in existing:
            if record.content == server_ip:
                self.delete_record(record=record)
                return True
        
        return False
    
    def list_servers(self, domain: str) -> List[str]:
        """
        List all server IPs for a domain.
        
        Args:
            domain: Full domain name
            
        Returns:
            List of IP addresses
        """
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
        
        Compares all A records in a zone against a set of active IPs.
        Deletes records pointing to IPs not in the active set.
        
        IMPORTANT: Only removes PROXIED records (orange cloud in Cloudflare).
        DNS-only records (gray cloud) are left untouched as they may be
        manually configured for other purposes.
        
        Args:
            zone: Zone name (e.g., "example.com")
            active_ips: Set of currently active IP addresses
            log_fn: Optional logging function
            
        Returns:
            Dict with cleanup stats:
            {
                "deleted": [{"name": str, "ip": str}, ...],
                "kept": int,
                "skipped_dns_only": int,
                "errors": [{"name": str, "ip": str, "error": str}, ...]
            }
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
                
                # Skip DNS-only records (not proxied) - these are likely manual entries
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
    
    def list_dns_records(self, zone_id: str) -> List[Dict[str, Any]]:
        """
        List all DNS records in a zone (raw API response).
        
        Args:
            zone_id: Cloudflare zone ID
            
        Returns:
            List of raw record dicts from API
        """
        result = self._request("GET", f"/zones/{zone_id}/dns_records")
        return result.get("result", [])
    
    def delete_dns_record(self, zone_id: str, record_id: str) -> bool:
        """
        Delete a DNS record by ID.
        
        Args:
            zone_id: Cloudflare zone ID
            record_id: DNS record ID
            
        Returns:
            True if deleted
        """
        self._request("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")
        return True
