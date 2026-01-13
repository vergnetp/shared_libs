"""
DigitalOcean Client - Server provisioning and management.

Clean interface for DO API operations.

SAFETY: This client protects unmanaged droplets by default.
All droplets created through this system are tagged with MANAGED_TAG.
list_droplets() only returns managed droplets unless include_unmanaged=True.
delete_droplet() refuses to delete unmanaged droplets unless force=True.
"""

from __future__ import annotations
import time
import requests
from pathlib import Path
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, Any, List, Optional
from enum import Enum

if TYPE_CHECKING:
    from ...context import DeploymentContext

from ...core.result import Result, ServerResult


# Tag applied to ALL droplets created through this system
# Used to protect personal/unmanaged servers from being listed or deleted
MANAGED_TAG = "deployed-via-api"


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
    vpc_uuid: Optional[str] = None
    image: Optional[Dict[str, Any]] = None
    
    @property
    def is_active(self) -> bool:
        return self.status == "active"
    
    @property
    def is_managed(self) -> bool:
        """Check if this droplet is managed by our system (has MANAGED_TAG)."""
        return MANAGED_TAG in (self.tags or [])
    
    @property
    def project(self) -> Optional[str]:
        """Extract project name from tags (project:xxx)."""
        for tag in self.tags:
            if tag.startswith("project:"):
                return tag[8:]
        return None
    
    @property
    def environment(self) -> Optional[str]:
        """Extract environment from tags (env:xxx)."""
        for tag in self.tags:
            if tag.startswith("env:"):
                return tag[4:]
        return None
    
    @property
    def service(self) -> Optional[str]:
        """Extract service name from tags (service:xxx)."""
        for tag in self.tags:
            if tag.startswith("service:"):
                return tag[8:]
        return None
    
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
            "vpc_uuid": self.vpc_uuid,
            "project": self.project,
            "environment": self.environment,
            "service": self.service,
            "image": self.image,
            "is_managed": self.is_managed,
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
            vpc_uuid=data.get("vpc_uuid"),
            image=data.get("image"),
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
    # Account
    # =========================================================================
    
    def get_account(self) -> Dict[str, Any]:
        """Get account info. Useful for verifying token validity."""
        result = self._get("/account")
        return result.get("account", {})
    
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
        auto_vpc: bool = True,
        project: Optional[str] = None,
        environment: str = "prod",
        wait: bool = True,
        wait_timeout: int = 300,
        node_agent_api_key: Optional[str] = None,
    ) -> Droplet:
        """
        Create a new droplet.
        
        Args:
            name: Droplet name
            region: Region slug (e.g., "lon1")
            size: Size slug (e.g., "s-1vcpu-1gb")
            image: Image slug or ID
            ssh_keys: SSH key IDs (uses default if not provided)
            tags: Additional tags to apply
            user_data: Cloud-init user data script
            vpc_uuid: VPC UUID (for private networking)
            auto_vpc: If True and vpc_uuid not provided, auto-create/use VPC for region
            project: Project name for tagging/filtering (e.g., "deploy-api", "hostomatic")
            environment: Environment name (e.g., "prod", "staging", "dev")
            wait: Wait for droplet to be active
            wait_timeout: Max seconds to wait
            node_agent_api_key: If provided, auto-generate cloud-init to set node agent API key
                               (for droplets created from snapshots with node agent pre-installed)
            
        Returns:
            Droplet object
        """
        # Auto-ensure VPC for internal networking between droplets
        if not vpc_uuid and auto_vpc:
            vpc_uuid = self.ensure_vpc(region=region)
        
        # Build tags - always include MANAGED_TAG, project and environment for filtering
        all_tags = list(tags or [])
        # CRITICAL: Always add MANAGED_TAG so we can identify our droplets
        if MANAGED_TAG not in all_tags:
            all_tags.append(MANAGED_TAG)
        if project:
            all_tags.append(f"project:{project}")
        if environment:
            all_tags.append(f"env:{environment}")
        
        # Auto-generate cloud-init for node agent API key if needed
        final_user_data = user_data
        if node_agent_api_key:
            api_key_script = f"""#!/bin/bash
# Set node agent API key (auto-generated by infra layer)
mkdir -p /etc/node-agent
echo "{node_agent_api_key}" > /etc/node-agent/api-key
chmod 600 /etc/node-agent/api-key
systemctl restart node_agent 2>/dev/null || true
"""
            if user_data:
                # Prepend API key setup to existing user_data
                if user_data.startswith("#!/"):
                    # Remove shebang from user_data, we'll use ours
                    lines = user_data.split("\n", 1)
                    final_user_data = api_key_script + "\n" + (lines[1] if len(lines) > 1 else "")
                else:
                    final_user_data = api_key_script + "\n" + user_data
            else:
                final_user_data = api_key_script
        
        data = {
            "name": name,
            "region": region,
            "size": size,
            "image": image,
            "ssh_keys": ssh_keys or self.default_ssh_keys,
            "tags": all_tags,
            "ipv6": False,
            "monitoring": True,
        }
        
        if final_user_data:
            data["user_data"] = final_user_data
        
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
        project: Optional[str] = None,
        environment: Optional[str] = None,
        service: Optional[str] = None,
        include_unmanaged: bool = False,
        page: int = 1,
        per_page: int = 100,
    ) -> List[Droplet]:
        """
        List droplets.
        
        SAFETY: By default, only returns droplets tagged with MANAGED_TAG.
        Personal/unmanaged servers are protected from accidental operations.
        Temporary snapshot-builder droplets are also excluded from listings.
        
        Args:
            tag: Filter by exact tag (overrides default MANAGED_TAG filter)
            project: Filter by project name (e.g., "deploy-api")
            environment: Filter by environment (e.g., "prod", "staging")
            service: Filter by service name (e.g., "deploy-api", "nginx")
            include_unmanaged: If True, return ALL droplets (use with caution!)
            page: Page number
            per_page: Results per page
            
        Returns:
            List of Droplet objects (only managed droplets unless include_unmanaged=True)
            
        Note: DO API only supports filtering by ONE tag at a time.
              Additional filters (project, environment, service) are applied client-side.
        """
        params = {"page": page, "per_page": per_page}
        
        # Determine which tag to use for API filtering
        # Priority: explicit tag > project tag > MANAGED_TAG (default safety)
        filter_tag = tag
        if project and not tag:
            filter_tag = f"project:{project}"
        elif environment and not tag and not project:
            filter_tag = f"env:{environment}"
        elif service and not tag and not project and not environment:
            filter_tag = f"service:{service}"
        elif not tag and not project and not environment and not service and not include_unmanaged:
            # Default: only show managed droplets
            filter_tag = MANAGED_TAG
        
        if filter_tag:
            params["tag_name"] = filter_tag
        
        result = self._get("/droplets", params=params)
        droplets = [Droplet.from_api(d) for d in result.get("droplets", [])]
        
        # Client-side filtering for additional constraints
        if project and environment:
            # Already filtered by project, now filter by environment
            env_tag = f"env:{environment}"
            droplets = [d for d in droplets if env_tag in (d.tags or [])]
        
        if service and (project or environment or tag):
            # Already filtered by something else, now filter by service
            service_tag = f"service:{service}"
            droplets = [d for d in droplets if service_tag in (d.tags or [])]
        
        # Extra safety: even with explicit tag filters, exclude unmanaged unless requested
        if not include_unmanaged and tag and tag != MANAGED_TAG:
            # User provided a specific tag, but we still filter to managed only
            droplets = [d for d in droplets if d.is_managed]
        
        # Always exclude temporary snapshot-builder droplets from listings
        # (they can still be deleted via delete_droplet which allows snapshot-builder tag)
        droplets = [d for d in droplets if "snapshot-builder" not in (d.tags or [])]
        
        return droplets
    
    def tag_droplet(self, droplet_id: int, tag: str) -> Result:
        """
        Add a tag to a droplet.
        
        Args:
            droplet_id: ID of the droplet to tag
            tag: Tag to add (e.g., "service:deploy-api")
            
        Returns:
            Result indicating success or failure
        """
        try:
            # First, create the tag if it doesn't exist
            try:
                self._post("/tags", {"name": tag})
            except DOAPIError:
                pass  # Tag likely already exists
            
            # Tag the droplet
            self._post(f"/tags/{tag}/resources", {
                "resources": [{"resource_id": str(droplet_id), "resource_type": "droplet"}]
            })
            return Result.ok(f"Tagged droplet {droplet_id} with '{tag}'")
        except DOAPIError as e:
            return Result.fail(f"Failed to tag droplet: {e}")
    
    def untag_droplet(self, droplet_id: int, tag: str) -> Result:
        """
        Remove a tag from a droplet.
        
        Args:
            droplet_id: ID of the droplet
            tag: Tag to remove
            
        Returns:
            Result indicating success or failure
        """
        try:
            # Use DELETE with body to remove tag from resource
            import requests
            resp = requests.delete(
                f"{self.base_url}/tags/{tag}/resources",
                headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
                json={"resources": [{"resource_id": str(droplet_id), "resource_type": "droplet"}]}
            )
            if resp.status_code not in (200, 204):
                return Result.fail(f"Failed to untag: {resp.text}")
            return Result.ok(f"Removed tag '{tag}' from droplet {droplet_id}")
        except Exception as e:
            return Result.fail(f"Failed to untag droplet: {e}")
    
    def delete_droplet(self, droplet_id: int, force: bool = False) -> Result:
        """
        Delete a droplet.
        
        SAFETY: Refuses to delete unmanaged droplets unless force=True.
        This protects personal servers from accidental deletion.
        
        Exception: Droplets with 'snapshot-builder' tag are always deletable
        (they're temporary builders created by our snapshot system).
        
        Args:
            droplet_id: ID of the droplet to delete
            force: If True, delete even if droplet is unmanaged (dangerous!)
            
        Returns:
            Result indicating success or failure
        """
        # Safety check: verify droplet is managed before deleting
        if not force:
            droplet = self.get_droplet(droplet_id)
            if droplet is None:
                return Result.fail(f"Droplet {droplet_id} not found")
            
            # Allow deletion if: managed OR has snapshot-builder tag (temporary)
            is_builder = "snapshot-builder" in (droplet.tags or [])
            if not droplet.is_managed and not is_builder:
                return Result.fail(
                    f"Droplet {droplet_id} ({droplet.name}) is not managed by this system. "
                    f"Missing tag '{MANAGED_TAG}'. Use force=True to delete anyway."
                )
        
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
    
    DEPLOYER_KEY_NAME = "deployer_key"
    # Use default SSH key location so `ssh` command finds it automatically
    DEPLOYER_KEY_PATH = Path.home() / ".ssh" / "id_ed25519"
    
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
    
    def ensure_deployer_key(self) -> str:
        """
        Ensure deployer SSH key exists locally and on DO.
        
        1. Creates ~/.ssh/deployer_id_rsa if missing
        2. Uploads to DO if not present
        3. Returns the DO key ID
        
        Returns:
            str: DigitalOcean SSH key ID
            
        Example:
            client = DOClient(token)
            key_id = client.ensure_deployer_key()
            client.create_droplet("myserver", ssh_keys=[key_id])
        """
        import subprocess
        
        private_key_path = self.DEPLOYER_KEY_PATH
        public_key_path = Path(str(private_key_path) + ".pub")
        
        # Step 1: Generate local key if missing
        if not private_key_path.exists():
            # Create .ssh directory if needed
            private_key_path.parent.mkdir(mode=0o700, exist_ok=True)
            
            # Generate ed25519 key (more secure, shorter)
            result = subprocess.run([
                "ssh-keygen",
                "-t", "ed25519",
                "-f", str(private_key_path),
                "-N", "",  # No passphrase
                "-C", "deployer@infra"
            ], capture_output=True, text=True)
            
            if result.returncode != 0:
                raise RuntimeError(f"Failed to generate SSH key: {result.stderr}")
            
            # Set permissions
            private_key_path.chmod(0o600)
            public_key_path.chmod(0o644)
        
        # Step 2: Read public key
        if not public_key_path.exists():
            raise FileNotFoundError(f"Public key not found: {public_key_path}")
        
        public_key = public_key_path.read_text().strip()
        
        # Step 3: Check if key exists on DO - check by CONTENT first (most reliable)
        existing_keys = self.list_ssh_keys()
        for key in existing_keys:
            if key.get("public_key", "").strip() == public_key:
                return str(key["id"])
        
        # Step 4: Upload to DO
        try:
            new_key = self.add_ssh_key(self.DEPLOYER_KEY_NAME, public_key)
            return str(new_key["id"])
        except Exception as e:
            # Handle race condition - key might have been created
            if "422" in str(e) or "already" in str(e).lower():
                existing_keys = self.list_ssh_keys()
                for key in existing_keys:
                    if key.get("public_key", "").strip() == public_key:
                        return str(key["id"])
            raise
    
    @classmethod
    def get_deployer_key_path(cls) -> Path:
        """Get path to deployer private key."""
        return cls.DEPLOYER_KEY_PATH
    
    # =========================================================================
    # VPC (Virtual Private Cloud)
    # =========================================================================
    
    def list_vpcs(self) -> List[Dict[str, Any]]:
        """List all VPCs."""
        result = self._get("/vpcs")
        return result.get("vpcs", [])
    
    def get_vpc(self, vpc_id: str) -> Optional[Dict[str, Any]]:
        """Get VPC by ID."""
        try:
            result = self._get(f"/vpcs/{vpc_id}")
            return result.get("vpc")
        except DOAPIError as e:
            if e.status_code == 404:
                return None
            raise
    
    def create_vpc(
        self,
        name: str,
        region: str,
        ip_range: str = "10.120.0.0/20",
        description: str = "",
    ) -> Dict[str, Any]:
        """
        Create a new VPC.
        
        Args:
            name: VPC name
            region: Region slug (e.g., "lon1")
            ip_range: Private IP range (default: 10.120.0.0/20 - avoids DO reserved ranges)
            description: Optional description
            
        Returns:
            VPC object from API
        """
        data = {
            "name": name,
            "region": region,
            "ip_range": ip_range,
        }
        if description:
            data["description"] = description
            
        result = self._post("/vpcs", data)
        return result.get("vpc", {})
    
    def delete_vpc(self, vpc_id: str) -> bool:
        """Delete a VPC (must have no members)."""
        try:
            self._delete(f"/vpcs/{vpc_id}")
            return True
        except DOAPIError:
            return False
    
    def get_vpc_members(self, vpc_id: str) -> List[Dict[str, Any]]:
        """List all resources in a VPC."""
        result = self._get(f"/vpcs/{vpc_id}/members")
        return result.get("members", [])
    
    def ensure_vpc(
        self,
        region: str,
        name_prefix: str = "deploy-api",
        ip_range: str = None,
    ) -> str:
        """
        Ensure a VPC exists for the given region.
        
        Creates one if it doesn't exist. Uses unique IP range per region.
        
        Args:
            region: Region slug
            name_prefix: Prefix for VPC name
            ip_range: Private IP range (auto-generated per region if not specified)
            
        Returns:
            VPC UUID
        """
        vpc_name = f"{name_prefix}-{region}"
        
        # Check existing VPCs
        vpcs = self.list_vpcs()
        for vpc in vpcs:
            if vpc.get("name") == vpc_name and vpc.get("region") == region:
                return vpc["id"]
        
        # Generate unique IP range per region if not specified
        if not ip_range:
            # Map regions to unique /20 blocks in 10.120-10.135 range
            region_offsets = {
                "lon1": 0, "ams3": 1, "fra1": 2, "blr1": 3,
                "nyc1": 4, "nyc3": 5, "sfo3": 6, "tor1": 7,
                "sgp1": 8, "syd1": 9,
            }
            offset = region_offsets.get(region, hash(region) % 16)
            ip_range = f"10.{120 + offset}.0.0/20"
        
        # Create new VPC
        try:
            vpc = self.create_vpc(
                name=vpc_name,
                region=region,
                ip_range=ip_range,
                description=f"Private network for {name_prefix} deployments",
            )
            return vpc["id"]
        except DOAPIError as e:
            # If VPC creation fails due to overlap, try to find existing VPC
            if "overlaps" in str(e).lower():
                # Refresh VPC list and try to find matching one
                vpcs = self.list_vpcs()
                for vpc in vpcs:
                    if vpc.get("region") == region:
                        # Use any existing VPC in this region
                        return vpc["id"]
            raise
    
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
    
    # =========================================================================
    # Snapshots
    # =========================================================================
    
    DOCKER_SNAPSHOT_NAME = "docker-base-ubuntu-24"
    
    def list_snapshots(
        self,
        resource_type: str = "droplet",
        page: int = 1,
        per_page: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        List snapshots.
        
        Args:
            resource_type: "droplet" or "volume"
            page: Page number
            per_page: Results per page
            
        Returns:
            List of snapshot objects
        """
        params = {
            "resource_type": resource_type,
            "page": page,
            "per_page": per_page,
        }
        result = self._get("/snapshots", params=params)
        return result.get("snapshots", [])
    
    def get_snapshot(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        """Get snapshot by ID."""
        try:
            result = self._get(f"/snapshots/{snapshot_id}")
            return result.get("snapshot")
        except DOAPIError as e:
            if e.status_code == 404:
                return None
            raise
    
    def get_snapshot_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get snapshot by name."""
        snapshots = self.list_snapshots()
        for snap in snapshots:
            if snap.get("name") == name:
                return snap
        return None
    
    def create_snapshot_from_droplet(
        self,
        droplet_id: int,
        name: str,
        wait: bool = True,
        wait_timeout: int = 600,
    ) -> Dict[str, Any]:
        """
        Create snapshot from a droplet.
        
        Args:
            droplet_id: Source droplet ID
            name: Snapshot name
            wait: Wait for snapshot to complete
            wait_timeout: Max seconds to wait
            
        Returns:
            Action object (contains snapshot info after completion)
        """
        result = self._post(f"/droplets/{droplet_id}/actions", {
            "type": "snapshot",
            "name": name,
        })
        
        action = result.get("action", {})
        
        if wait and action.get("id"):
            action = self._wait_for_action(action["id"], wait_timeout)
        
        return action
    
    def delete_snapshot(self, snapshot_id: str) -> Result:
        """Delete a snapshot."""
        try:
            self._delete(f"/snapshots/{snapshot_id}")
            return Result.ok(f"Snapshot {snapshot_id} deleted")
        except DOAPIError as e:
            return Result.fail(str(e))
    
    def transfer_snapshot(
        self,
        snapshot_id: str,
        region: str,
        wait: bool = True,
        wait_timeout: int = 600,
    ) -> Dict[str, Any]:
        """
        Transfer a snapshot to another region.
        
        Args:
            snapshot_id: Snapshot ID to transfer
            region: Target region slug (e.g., "nyc1")
            wait: Wait for transfer to complete
            wait_timeout: Max seconds to wait
            
        Returns:
            Action object
        """
        # Snapshots are images in DO API
        result = self._post(f"/images/{snapshot_id}/actions", {
            "type": "transfer",
            "region": region,
        })
        
        action = result.get("action", {})
        
        if wait and action.get("id"):
            action = self._wait_for_action(action["id"], wait_timeout)
        
        return action
    
    def transfer_snapshot_to_all_regions(
        self,
        snapshot_id: str,
        wait: bool = False,
    ) -> Dict[str, Any]:
        """
        Transfer a snapshot to all available regions.
        
        Args:
            snapshot_id: Snapshot ID to transfer
            wait: Wait for ALL transfers to complete (can take a long time)
            
        Returns:
            Dict with transfer status per region
        """
        # Get current snapshot
        snapshot = self.get_snapshot(snapshot_id)
        if not snapshot:
            raise DOAPIError(f"Snapshot {snapshot_id} not found")
        
        current_regions = set(snapshot.get("regions", []))
        
        # Get all available regions
        all_regions = self.list_regions()
        available_regions = {r["slug"] for r in all_regions if r.get("available", True)}
        
        # Filter to regions we need to transfer to
        target_regions = available_regions - current_regions
        
        results = {
            "snapshot_id": snapshot_id,
            "snapshot_name": snapshot.get("name"),
            "already_in": list(current_regions),
            "transferring_to": list(target_regions),
            "actions": [],
        }
        
        # Start transfers
        for region in target_regions:
            try:
                action = self.transfer_snapshot(
                    snapshot_id=snapshot_id,
                    region=region,
                    wait=wait,
                    wait_timeout=900,  # 15 min per region
                )
                results["actions"].append({
                    "region": region,
                    "action_id": action.get("id"),
                    "status": action.get("status", "in-progress"),
                })
            except DOAPIError as e:
                results["actions"].append({
                    "region": region,
                    "error": str(e),
                    "status": "failed",
                })
        
        return results
    
    def list_regions(self) -> List[Dict[str, Any]]:
        """List all available regions."""
        result = self._get("/regions")
        return result.get("regions", [])
    
    def get_action(self, action_id: int) -> Dict[str, Any]:
        """Get action status."""
        result = self._get(f"/actions/{action_id}")
        return result.get("action", {})
    
    def _wait_for_action(
        self,
        action_id: int,
        timeout: int = 600,
    ) -> Dict[str, Any]:
        """Wait for an action to complete."""
        start = time.time()
        
        while time.time() - start < timeout:
            result = self._get(f"/actions/{action_id}")
            action = result.get("action", {})
            
            status = action.get("status", "")
            if status == "completed":
                return action
            elif status == "errored":
                raise DOAPIError(f"Action {action_id} failed")
            
            time.sleep(10)
        
        raise DOAPIError(f"Action {action_id} did not complete in {timeout}s")
    
    def ensure_docker_snapshot(
        self,
        region: str = "lon1",
        size: str = "s-1vcpu-1gb",
    ) -> str:
        """
        Ensure a Docker-ready snapshot exists.
        
        If not found:
        1. Creates a temporary droplet
        2. Installs Docker
        3. Pulls common images (postgres, redis)
        4. Creates snapshot
        5. Deletes temp droplet
        
        Args:
            region: Region for temp droplet
            size: Size for temp droplet
            
        Returns:
            Snapshot ID to use for new droplets
        """
        # Check if snapshot already exists
        existing = self.get_snapshot_by_name(self.DOCKER_SNAPSHOT_NAME)
        if existing:
            return str(existing["id"])
        
        # Create temporary droplet
        user_data = '''#!/bin/bash
set -e

# Install Docker
curl -fsSL https://get.docker.com | sh

# Enable Docker service
systemctl enable docker
systemctl start docker

# Pull common images
docker pull postgres:15-alpine
docker pull redis:7-alpine
docker pull nginx:alpine

# Clean up
apt-get clean
rm -rf /var/lib/apt/lists/*

# Signal completion
touch /tmp/docker-setup-complete
'''
        
        temp_name = f"docker-snapshot-builder-{int(time.time())}"
        
        droplet = self.create_droplet(
            name=temp_name,
            region=region,
            size=size,
            user_data=user_data,
            wait=True,
        )
        
        # Wait for cloud-init to complete (Docker install)
        # This is a bit hacky but works
        import subprocess
        
        ssh_key_path = self.DEPLOYER_KEY_PATH
        max_wait = 300
        start = time.time()
        
        while time.time() - start < max_wait:
            try:
                result = subprocess.run(
                    [
                        "ssh",
                        "-i", str(ssh_key_path),
                        "-o", "StrictHostKeyChecking=no",
                        "-o", "UserKnownHostsFile=/dev/null",
                        "-o", "ConnectTimeout=10",
                        f"root@{droplet.ip}",
                        "test -f /tmp/docker-setup-complete && docker --version"
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                
                if result.returncode == 0 and "Docker version" in result.stdout:
                    break
            except Exception:
                pass
            
            time.sleep(10)
        else:
            # Cleanup and fail
            self.delete_droplet(droplet.id)
            raise DOAPIError("Docker setup did not complete in time")
        
        # Power off droplet before snapshot (recommended)
        self._post(f"/droplets/{droplet.id}/actions", {"type": "power_off"})
        time.sleep(10)
        
        # Create snapshot
        action = self.create_snapshot_from_droplet(
            droplet.id,
            self.DOCKER_SNAPSHOT_NAME,
            wait=True,
        )
        
        # Delete temp droplet
        self.delete_droplet(droplet.id)
        
        # Find the snapshot ID from completed action
        # The snapshot ID is in the resource_id after completion
        snapshot = self.get_snapshot_by_name(self.DOCKER_SNAPSHOT_NAME)
        if snapshot:
            return str(snapshot["id"])
        
        raise DOAPIError("Failed to find created snapshot")
    
    # =========================================================================
    # Container Registry
    # =========================================================================
    
    def get_registry(self) -> Optional[Dict]:
        """Get container registry info. Returns None if not created."""
        try:
            resp = self._get("/registry")
            return resp.get("registry")
        except DOAPIError as e:
            if e.status_code == 404:
                return None
            raise
    
    def create_registry(self, name: str, region: str = "fra1") -> Dict:
        """
        Create a container registry.
        
        Args:
            name: Registry name (globally unique, lowercase alphanumeric + hyphens)
            region: Region slug (fra1, nyc3, sgp1, sfo3)
        
        Returns:
            Registry info dict
        """
        # DO has subscription tiers: starter (free, 500MB), basic, professional
        resp = self._post("/registry", {
            "name": name,
            "subscription_tier_slug": "starter",
            "region": region,
        })
        return resp.get("registry", {})
    
    def ensure_registry(self, name: str = None, region: str = "fra1") -> Dict:
        """Get existing registry or create one."""
        registry = self.get_registry()
        if registry:
            return registry
        
        # Generate name from token hash if not provided
        if not name:
            import hashlib
            name = "reg-" + hashlib.sha256(self.api_token.encode()).hexdigest()[:12]
        
        return self.create_registry(name, region)
    
    def get_registry_credentials(self, read_write: bool = True, expiry_seconds: int = 3600) -> Dict:
        """
        Get Docker credentials for registry login.
        
        Returns:
            {
                "auths": {
                    "registry.digitalocean.com": {
                        "auth": "base64-encoded-creds"
                    }
                }
            }
        """
        resp = self._get("/registry/docker-credentials", params={
            "read_write": str(read_write).lower(),
            "expiry_seconds": expiry_seconds,
        })
        return resp
    
    def list_registry_repositories(self) -> List[Dict]:
        """List all repositories in registry."""
        registry = self.get_registry()
        if not registry:
            return []
        
        name = registry.get("name")
        resp = self._get(f"/registry/{name}/repositoriesV2")
        return resp.get("repositories", [])
    
    def list_repository_tags(self, repository: str) -> List[Dict]:
        """List tags for a repository."""
        registry = self.get_registry()
        if not registry:
            return []
        
        name = registry.get("name")
        resp = self._get(f"/registry/{name}/repositories/{repository}/tags")
        return resp.get("tags", [])
    
    def delete_repository_tag(self, repository: str, tag: str) -> bool:
        """Delete a specific tag from repository."""
        registry = self.get_registry()
        if not registry:
            return False
        
        name = registry.get("name")
        self._delete(f"/registry/{name}/repositories/{repository}/tags/{tag}")
        return True
    
    def get_registry_endpoint(self) -> Optional[str]:
        """Get the registry endpoint URL for docker push/pull."""
        registry = self.get_registry()
        if not registry:
            return None
        
        # DO registry format: registry.digitalocean.com/{registry_name}
        return f"registry.digitalocean.com/{registry.get('name')}"


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
        from ...utils.naming import sanitize_for_dns
        
        tags = [
            f"user-{sanitize_for_dns(self.ctx.user_id)}",
            f"project-{sanitize_for_dns(self.ctx.project_name)}",
            f"env-{sanitize_for_dns(self.ctx.env)}",
            "managed-by-deploy-api",
        ]
        if service:
            tags.append(f"service-{sanitize_for_dns(service)}")
        return tags
    
    def _make_name(self, service: str, index: int) -> str:
        """Generate droplet name (DO only allows alphanumeric and hyphens)."""
        from ...utils.naming import sanitize_for_dns
        
        namespace = sanitize_for_dns(self.ctx.namespace)
        service = sanitize_for_dns(service)
        return f"{namespace}-{service}-{index}"
    
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
