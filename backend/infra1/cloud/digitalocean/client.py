"""
DigitalOcean Client - Server provisioning and management.

Clean interface for DO API operations.
"""

from __future__ import annotations
import time
import requests
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, Any, List, Optional
from enum import Enum

if TYPE_CHECKING:
    from ...context import DeploymentContext

from ...core.result import Result, ServerResult


class DropletSize(Enum):
    """Common droplet sizes."""
    S_1CPU_1GB = "s-1vcpu-1gb"      # $6/mo
    S_1CPU_2GB = "s-1vcpu-2gb"      # $12/mo
    S_2CPU_2GB = "s-2vcpu-2gb"      # $18/mo
    S_2CPU_4GB = "s-2vcpu-4gb"      # $24/mo
    S_4CPU_8GB = "s-4vcpu-8gb"      # $48/mo
    S_8CPU_16GB = "s-8vcpu-16gb"    # $96/mo


class Region(Enum):
    """DO regions."""
    LON1 = "lon1"  # London
    NYC1 = "nyc1"  # New York 1
    NYC3 = "nyc3"  # New York 3
    SFO3 = "sfo3"  # San Francisco
    AMS3 = "ams3"  # Amsterdam
    SGP1 = "sgp1"  # Singapore
    FRA1 = "fra1"  # Frankfurt


@dataclass
class Droplet:
    """Droplet (server) info."""
    id: int
    name: str
    ip: Optional[str]
    private_ip: Optional[str]
    region: str
    size: str
    status: str
    tags: List[str] = field(default_factory=list)
    created_at: Optional[str] = None
    
    @property
    def is_active(self) -> bool:
        return self.status == "active"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "ip": self.ip,
            "private_ip": self.private_ip,
            "region": self.region,
            "size": self.size,
            "status": self.status,
            "tags": self.tags,
            "created_at": self.created_at,
        }
    
    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> 'Droplet':
        """Create from DO API response."""
        # Extract public IPv4
        ip = None
        private_ip = None
        for network in data.get("networks", {}).get("v4", []):
            if network.get("type") == "public":
                ip = network.get("ip_address")
            elif network.get("type") == "private":
                private_ip = network.get("ip_address")
        
        return cls(
            id=data["id"],
            name=data["name"],
            ip=ip,
            private_ip=private_ip,
            region=data.get("region", {}).get("slug", ""),
            size=data.get("size", {}).get("slug", ""),
            status=data.get("status", ""),
            tags=data.get("tags", []),
            created_at=data.get("created_at"),
        )


class DOClient:
    """
    DigitalOcean API client.
    
    Usage:
        do = DOClient(api_token="xxx")
        
        # Create droplet
        droplet = do.create_droplet(
            name="myapp-api-1",
            region="lon1",
            size="s-1vcpu-1gb",
            tags=["myapp", "prod", "api"],
        )
        
        # List droplets
        droplets = do.list_droplets(tag="myapp")
        
        # Delete droplet
        do.delete_droplet(droplet.id)
    """
    
    BASE_URL = "https://api.digitalocean.com/v2"
    DEFAULT_IMAGE = "ubuntu-24-04-x64"
    
    def __init__(
        self, 
        api_token: str,
        default_ssh_keys: Optional[List[str]] = None,
    ):
        """
        Initialize DO client.
        
        Args:
            api_token: DigitalOcean API token
            default_ssh_keys: Default SSH key IDs/fingerprints to add to droplets
        """
        self.api_token = api_token
        self.default_ssh_keys = default_ssh_keys or []
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        })
    
    # =========================================================================
    # HTTP Helpers
    # =========================================================================
    
    def _request(
        self, 
        method: str, 
        path: str, 
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Make API request."""
        url = f"{self.BASE_URL}{path}"
        
        response = self._session.request(
            method=method,
            url=url,
            json=data,
            params=params,
            timeout=30,
        )
        
        if response.status_code == 204:
            return {}
        
        result = response.json()
        
        if response.status_code >= 400:
            error_msg = result.get("message", str(result))
            raise DOAPIError(error_msg, response.status_code)
        
        return result
    
    def _get(self, path: str, params: Optional[Dict] = None) -> Dict:
        return self._request("GET", path, params=params)
    
    def _post(self, path: str, data: Dict) -> Dict:
        return self._request("POST", path, data=data)
    
    def _delete(self, path: str) -> Dict:
        return self._request("DELETE", path)
    
    # =========================================================================
    # Droplets
    # =========================================================================
    
    def create_droplet(
        self,
        name: str,
        region: str = "lon1",
        size: str = "s-1vcpu-1gb",
        image: str = DEFAULT_IMAGE,
        ssh_keys: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        user_data: Optional[str] = None,
        vpc_uuid: Optional[str] = None,
        wait: bool = True,
        wait_timeout: int = 300,
    ) -> Droplet:
        """
        Create a new droplet.
        
        Args:
            name: Droplet name
            region: Region slug (e.g., "lon1")
            size: Size slug (e.g., "s-1vcpu-1gb")
            image: Image slug or ID
            ssh_keys: SSH key IDs (uses default if not provided)
            tags: Tags to apply
            user_data: Cloud-init user data script
            vpc_uuid: VPC UUID (for private networking)
            wait: Wait for droplet to be active
            wait_timeout: Max seconds to wait
            
        Returns:
            Droplet object
        """
        data = {
            "name": name,
            "region": region,
            "size": size,
            "image": image,
            "ssh_keys": ssh_keys or self.default_ssh_keys,
            "tags": tags or [],
            "ipv6": False,
            "monitoring": True,
        }
        
        if user_data:
            data["user_data"] = user_data
        
        if vpc_uuid:
            data["vpc_uuid"] = vpc_uuid
        
        result = self._post("/droplets", data)
        droplet = Droplet.from_api(result["droplet"])
        
        if wait:
            droplet = self._wait_for_droplet(droplet.id, wait_timeout)
        
        return droplet
    
    def get_droplet(self, droplet_id: int) -> Optional[Droplet]:
        """Get droplet by ID."""
        try:
            result = self._get(f"/droplets/{droplet_id}")
            return Droplet.from_api(result["droplet"])
        except DOAPIError as e:
            if e.status_code == 404:
                return None
            raise
    
    def list_droplets(
        self,
        tag: Optional[str] = None,
        page: int = 1,
        per_page: int = 100,
    ) -> List[Droplet]:
        """
        List droplets.
        
        Args:
            tag: Filter by tag
            page: Page number
            per_page: Results per page
            
        Returns:
            List of Droplet objects
        """
        params = {"page": page, "per_page": per_page}
        
        if tag:
            params["tag_name"] = tag
        
        result = self._get("/droplets", params=params)
        
        return [Droplet.from_api(d) for d in result.get("droplets", [])]
    
    def delete_droplet(self, droplet_id: int) -> Result:
        """Delete a droplet."""
        try:
            self._delete(f"/droplets/{droplet_id}")
            return Result.ok(f"Droplet {droplet_id} deleted")
        except DOAPIError as e:
            return Result.fail(str(e))
    
    def _wait_for_droplet(
        self, 
        droplet_id: int, 
        timeout: int = 300,
    ) -> Droplet:
        """Wait for droplet to become active."""
        start = time.time()
        
        while time.time() - start < timeout:
            droplet = self.get_droplet(droplet_id)
            
            if droplet and droplet.is_active and droplet.ip:
                return droplet
            
            time.sleep(5)
        
        raise DOAPIError(f"Droplet {droplet_id} did not become active in {timeout}s")
    
    # =========================================================================
    # SSH Keys
    # =========================================================================
    
    def list_ssh_keys(self) -> List[Dict[str, Any]]:
        """List all SSH keys."""
        result = self._get("/account/keys")
        return result.get("ssh_keys", [])
    
    def add_ssh_key(self, name: str, public_key: str) -> Dict[str, Any]:
        """Add SSH key to account."""
        result = self._post("/account/keys", {
            "name": name,
            "public_key": public_key,
        })
        return result.get("ssh_key", {})
    
    # =========================================================================
    # Regions & Sizes
    # =========================================================================
    
    def list_regions(self) -> List[Dict[str, Any]]:
        """List available regions."""
        result = self._get("/regions")
        return [r for r in result.get("regions", []) if r.get("available")]
    
    def list_sizes(self) -> List[Dict[str, Any]]:
        """List available sizes."""
        result = self._get("/sizes")
        return result.get("sizes", [])
    
    # =========================================================================
    # Firewall
    # =========================================================================
    
    def create_firewall(
        self,
        name: str,
        droplet_ids: Optional[List[int]] = None,
        tags: Optional[List[str]] = None,
        inbound_rules: Optional[List[Dict]] = None,
        outbound_rules: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """
        Create firewall.
        
        Default rules allow SSH (22), HTTP (80), HTTPS (443).
        """
        if inbound_rules is None:
            inbound_rules = [
                # SSH
                {"protocol": "tcp", "ports": "22", "sources": {"addresses": ["0.0.0.0/0"]}},
                # HTTP
                {"protocol": "tcp", "ports": "80", "sources": {"addresses": ["0.0.0.0/0"]}},
                # HTTPS
                {"protocol": "tcp", "ports": "443", "sources": {"addresses": ["0.0.0.0/0"]}},
                # Private network (10.x.x.x)
                {"protocol": "tcp", "ports": "all", "sources": {"addresses": ["10.0.0.0/8"]}},
            ]
        
        if outbound_rules is None:
            outbound_rules = [
                # Allow all outbound
                {"protocol": "tcp", "ports": "all", "destinations": {"addresses": ["0.0.0.0/0"]}},
                {"protocol": "udp", "ports": "all", "destinations": {"addresses": ["0.0.0.0/0"]}},
                {"protocol": "icmp", "destinations": {"addresses": ["0.0.0.0/0"]}},
            ]
        
        data = {
            "name": name,
            "inbound_rules": inbound_rules,
            "outbound_rules": outbound_rules,
        }
        
        if droplet_ids:
            data["droplet_ids"] = droplet_ids
        if tags:
            data["tags"] = tags
        
        result = self._post("/firewalls", data)
        return result.get("firewall", {})
    
    # =========================================================================
    # DNS
    # =========================================================================
    
    def create_domain_record(
        self,
        domain: str,
        record_type: str,
        name: str,
        data: str,
        ttl: int = 300,
    ) -> Dict[str, Any]:
        """
        Create DNS record.
        
        Args:
            domain: Domain name (e.g., "example.com")
            record_type: A, AAAA, CNAME, etc.
            name: Record name (e.g., "api" for api.example.com)
            data: Record value (e.g., IP address)
            ttl: Time to live in seconds
        """
        result = self._post(f"/domains/{domain}/records", {
            "type": record_type,
            "name": name,
            "data": data,
            "ttl": ttl,
        })
        return result.get("domain_record", {})
    
    def list_domain_records(self, domain: str) -> List[Dict[str, Any]]:
        """List DNS records for domain."""
        result = self._get(f"/domains/{domain}/records")
        return result.get("domain_records", [])


class DOAPIError(Exception):
    """DigitalOcean API error."""
    
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


# =========================================================================
# Context-Aware Wrapper
# =========================================================================

class ServerManager:
    """
    Context-aware server manager.
    
    Wraps DOClient with deployment context for automatic tagging, 
    naming, and storage integration.
    
    Usage:
        manager = ServerManager(ctx, do_token="xxx")
        
        # Provision servers for a service
        servers = manager.provision(
            service="api",
            count=3,
            size="s-2vcpu-4gb",
        )
        
        # List servers for project
        servers = manager.list_servers()
        
        # Cleanup unused servers
        manager.cleanup()
    """
    
    def __init__(
        self, 
        ctx: 'DeploymentContext',
        do_token: Optional[str] = None,
    ):
        """
        Initialize server manager.
        
        Args:
            ctx: Deployment context
            do_token: DO API token (or from credentials)
        """
        self.ctx = ctx
        self._do_token = do_token
        self._client: Optional[DOClient] = None
    
    async def _get_client(self) -> DOClient:
        """Get or create DO client."""
        if self._client is None:
            token = self._do_token
            
            if not token and self.ctx.storage:
                creds = await self.ctx.storage.get_credentials(
                    self.ctx.user_id,
                    self.ctx.project_name,
                    self.ctx.env,
                )
                token = creds.get("digitalocean_token") if creds else None
            
            if not token:
                raise ValueError("DigitalOcean API token required")
            
            self._client = DOClient(token)
        
        return self._client
    
    def _make_tags(self, service: Optional[str] = None) -> List[str]:
        """Generate tags for a droplet."""
        tags = [
            f"user:{self.ctx.user_id}",
            f"project:{self.ctx.project_name}",
            f"env:{self.ctx.env}",
        ]
        if service:
            tags.append(f"service:{service}")
        return tags
    
    def _make_name(self, service: str, index: int) -> str:
        """Generate droplet name."""
        return f"{self.ctx.namespace}-{service}-{index}"
    
    async def provision(
        self,
        service: str,
        count: int = 1,
        size: str = "s-1vcpu-1gb",
        region: Optional[str] = None,
    ) -> List[Droplet]:
        """
        Provision servers for a service.
        
        Args:
            service: Service name
            count: Number of servers
            size: Droplet size
            region: Region (defaults to ctx.default_zone)
            
        Returns:
            List of created droplets
        """
        client = await self._get_client()
        region = region or self.ctx.default_zone
        tags = self._make_tags(service)
        
        droplets = []
        for i in range(count):
            name = self._make_name(service, i + 1)
            
            self.ctx.log_info(f"Creating droplet {name}", region=region, size=size)
            
            droplet = client.create_droplet(
                name=name,
                region=region,
                size=size,
                tags=tags,
                wait=True,
            )
            
            droplets.append(droplet)
            
            # Save to storage
            if self.ctx.storage:
                await self.ctx.storage.save_server(
                    self.ctx.user_id,
                    {
                        "id": str(droplet.id),
                        "ip": droplet.ip,
                        "private_ip": droplet.private_ip,
                        "zone": droplet.region,
                        "status": "active",
                        "droplet_id": str(droplet.id),
                        "hostname": droplet.name,
                        "project_name": self.ctx.project_name,
                        "env": self.ctx.env,
                        "service": service,
                        "tags": droplet.tags,
                    }
                )
            
            self.ctx.log_info(f"Created droplet {name}", ip=droplet.ip)
        
        return droplets
    
    async def list_servers(
        self,
        service: Optional[str] = None,
    ) -> List[Droplet]:
        """List servers for this project/env."""
        client = await self._get_client()
        
        # Use project tag to filter
        tag = f"project:{self.ctx.project_name}"
        droplets = client.list_droplets(tag=tag)
        
        # Filter by env
        env_tag = f"env:{self.ctx.env}"
        droplets = [d for d in droplets if env_tag in d.tags]
        
        # Filter by service if specified
        if service:
            service_tag = f"service:{service}"
            droplets = [d for d in droplets if service_tag in d.tags]
        
        return droplets
    
    async def delete_server(self, droplet_id: int) -> Result:
        """Delete a server."""
        client = await self._get_client()
        result = client.delete_droplet(droplet_id)
        
        if result.success and self.ctx.storage:
            await self.ctx.storage.delete_server(
                self.ctx.user_id,
                str(droplet_id),
            )
        
        return result
