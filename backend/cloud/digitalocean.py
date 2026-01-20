"""
DigitalOcean Client - Server provisioning and management.

Sync and async clients with retry, circuit breaker, and tracing.

SAFETY: This client protects unmanaged droplets by default.
All droplets created through this system are tagged with MANAGED_TAG.
list_droplets() only returns managed droplets unless include_unmanaged=True.
delete_droplet() refuses to delete unmanaged droplets unless force=True.

Usage:
    # Sync
    from cloud import DOClient
    
    client = DOClient(api_token="xxx")
    droplets = client.list_droplets()
    
    # Async
    from cloud import AsyncDOClient
    
    async with AsyncDOClient(api_token="xxx") as client:
        droplets = await client.list_droplets()
"""

from __future__ import annotations
import time
import asyncio
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum

from .base import BaseCloudClient, AsyncBaseCloudClient, CloudClientConfig
from .errors import DOError, NotFoundError


# Tag applied to ALL droplets created through this system
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


# =============================================================================
# Result Type (for operations that can fail gracefully)
# =============================================================================

@dataclass
class Result:
    """Operation result with success/failure state."""
    success: bool
    message: str
    data: Any = None
    
    @classmethod
    def ok(cls, message: str = "Success", data: Any = None) -> 'Result':
        return cls(success=True, message=message, data=data)
    
    @classmethod
    def fail(cls, message: str, data: Any = None) -> 'Result':
        return cls(success=False, message=message, data=data)


# =============================================================================
# Sync Client
# =============================================================================

class DOClient(BaseCloudClient):
    """
    DigitalOcean API client (sync).
    
    Usage:
        client = DOClient(api_token="xxx")
        
        # Create droplet
        droplet = client.create_droplet(
            name="myapp-api-1",
            region="lon1",
            size="s-1vcpu-1gb",
        )
        
        # List droplets
        droplets = client.list_droplets()
        
        # Delete droplet
        client.delete_droplet(droplet.id)
    """
    
    PROVIDER = "DigitalOcean"
    BASE_URL = "https://api.digitalocean.com/v2"
    DEFAULT_IMAGE = "ubuntu-24-04-x64"
    
    def __init__(
        self,
        api_token: str,
        default_ssh_keys: Optional[List[str]] = None,
        config: CloudClientConfig = None,
    ):
        super().__init__(api_token, config)
        self.default_ssh_keys = default_ssh_keys or []
    
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
        response = self._client.request(
            method=method,
            url=path,
            json=data,
            params=params,
            raise_on_error=False,
        )
        
        if response.status_code == 204:
            return {}
        
        result = response.json() if response.body else {}
        
        if response.status_code >= 400:
            error_msg = result.get("message", str(result))
            raise DOError(error_msg, response.status_code, result)
        
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
            auto_vpc: If True and vpc_uuid not provided, auto-create/use VPC
            project: Project name for tagging/filtering
            environment: Environment name (e.g., "prod", "staging")
            wait: Wait for droplet to be active
            wait_timeout: Max seconds to wait
            node_agent_api_key: If provided, auto-generate cloud-init for node agent
            
        Returns:
            Droplet object
        """
        # Auto-ensure VPC
        if not vpc_uuid and auto_vpc:
            vpc_uuid = self.ensure_vpc(region=region)
        
        # Build tags
        all_tags = list(tags or [])
        if MANAGED_TAG not in all_tags:
            all_tags.append(MANAGED_TAG)
        if project:
            all_tags.append(f"project:{project}")
        if environment:
            all_tags.append(f"env:{environment}")
        
        # Auto-generate cloud-init for node agent API key
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
                if user_data.startswith("#!/"):
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
        except DOError as e:
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
        """
        params = {"page": page, "per_page": per_page}
        
        # Determine filter tag
        filter_tag = tag
        if project and not tag:
            filter_tag = f"project:{project}"
        elif environment and not tag and not project:
            filter_tag = f"env:{environment}"
        elif service and not tag and not project and not environment:
            filter_tag = f"service:{service}"
        elif not tag and not project and not environment and not service and not include_unmanaged:
            filter_tag = MANAGED_TAG
        
        if filter_tag:
            params["tag_name"] = filter_tag
        
        result = self._get("/droplets", params=params)
        droplets = [Droplet.from_api(d) for d in result.get("droplets", [])]
        
        # Client-side filtering
        if project and environment:
            env_tag = f"env:{environment}"
            droplets = [d for d in droplets if env_tag in (d.tags or [])]
        
        if service and (project or environment or tag):
            service_tag = f"service:{service}"
            droplets = [d for d in droplets if service_tag in (d.tags or [])]
        
        # Extra safety
        if not include_unmanaged and tag and tag != MANAGED_TAG:
            droplets = [d for d in droplets if d.is_managed]
        
        # Exclude snapshot-builder droplets
        droplets = [d for d in droplets if "snapshot-builder" not in (d.tags or [])]
        
        return droplets
    
    def tag_droplet(self, droplet_id: int, tag: str) -> Result:
        """Add a tag to a droplet."""
        try:
            try:
                self._post("/tags", {"name": tag})
            except DOError:
                pass  # Tag likely exists
            
            self._post(f"/tags/{tag}/resources", {
                "resources": [{"resource_id": str(droplet_id), "resource_type": "droplet"}]
            })
            return Result.ok(f"Tagged droplet {droplet_id} with '{tag}'")
        except DOError as e:
            return Result.fail(f"Failed to tag droplet: {e}")
    
    def power_off_droplet(self, droplet_id: int, wait: bool = False, timeout: int = 120) -> None:
        """
        Power off a droplet.
        
        Required before creating a snapshot for a clean disk state.
        
        Args:
            droplet_id: Droplet ID to power off
            wait: If True, wait for power off to complete
            timeout: Max seconds to wait (only if wait=True)
        """
        result = self._post(f"/droplets/{droplet_id}/actions", {"type": "power_off"})
        
        if wait and result.get("action", {}).get("id"):
            action_id = result["action"]["id"]
            self._wait_for_action(action_id, timeout)
    
    def delete_droplet(self, droplet_id: int, force: bool = False) -> Result:
        """
        Delete a droplet.
        
        SAFETY: Refuses to delete unmanaged droplets unless force=True.
        """
        if not force:
            droplet = self.get_droplet(droplet_id)
            if droplet is None:
                return Result.fail(f"Droplet {droplet_id} not found")
            
            is_builder = "snapshot-builder" in (droplet.tags or [])
            if not droplet.is_managed and not is_builder:
                return Result.fail(
                    f"Droplet {droplet_id} ({droplet.name}) is not managed. "
                    f"Missing tag '{MANAGED_TAG}'. Use force=True to delete anyway."
                )
        
        try:
            self._delete(f"/droplets/{droplet_id}")
            return Result.ok(f"Droplet {droplet_id} deleted")
        except DOError as e:
            return Result.fail(str(e))
    
    def _wait_for_droplet(self, droplet_id: int, timeout: int = 300) -> Droplet:
        """Wait for droplet to become active."""
        start = time.time()
        
        while time.time() - start < timeout:
            droplet = self.get_droplet(droplet_id)
            if droplet and droplet.is_active and droplet.ip:
                return droplet
            time.sleep(5)
        
        raise DOError(f"Droplet {droplet_id} did not become active in {timeout}s")
    
    # =========================================================================
    # SSH Keys
    # =========================================================================
    
    DEPLOYER_KEY_NAME = "deployer_key"
    DEPLOYER_KEY_PATH = Path.home() / ".ssh" / "id_ed25519"
    
    def list_ssh_keys(self) -> List[Dict[str, Any]]:
        """List all SSH keys."""
        result = self._get("/account/keys")
        return result.get("ssh_keys", [])
    
    def add_ssh_key(self, name: str, public_key: str) -> Dict[str, Any]:
        """Add SSH key to account."""
        result = self._post("/account/keys", {"name": name, "public_key": public_key})
        return result.get("ssh_key", {})
    
    def ensure_deployer_key(self) -> str:
        """Ensure deployer SSH key exists locally and on DO."""
        import subprocess
        
        private_key_path = self.DEPLOYER_KEY_PATH
        public_key_path = Path(str(private_key_path) + ".pub")
        
        if not private_key_path.exists():
            private_key_path.parent.mkdir(mode=0o700, exist_ok=True)
            result = subprocess.run([
                "ssh-keygen", "-t", "ed25519",
                "-f", str(private_key_path),
                "-N", "", "-C", "deployer@infra"
            ], capture_output=True, text=True)
            
            if result.returncode != 0:
                raise RuntimeError(f"Failed to generate SSH key: {result.stderr}")
            
            private_key_path.chmod(0o600)
            public_key_path.chmod(0o644)
        
        if not public_key_path.exists():
            raise FileNotFoundError(f"Public key not found: {public_key_path}")
        
        public_key = public_key_path.read_text().strip()
        
        existing_keys = self.list_ssh_keys()
        for key in existing_keys:
            if key.get("public_key", "").strip() == public_key:
                return str(key["id"])
        
        try:
            new_key = self.add_ssh_key(self.DEPLOYER_KEY_NAME, public_key)
            return str(new_key["id"])
        except Exception as e:
            if "422" in str(e) or "already" in str(e).lower():
                existing_keys = self.list_ssh_keys()
                for key in existing_keys:
                    if key.get("public_key", "").strip() == public_key:
                        return str(key["id"])
            raise
    
    # =========================================================================
    # VPC
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
        except DOError as e:
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
        """Create a new VPC."""
        data = {"name": name, "region": region, "ip_range": ip_range}
        if description:
            data["description"] = description
        result = self._post("/vpcs", data)
        return result.get("vpc", {})
    
    def delete_vpc(self, vpc_id: str) -> bool:
        """Delete a VPC."""
        try:
            self._delete(f"/vpcs/{vpc_id}")
            return True
        except DOError:
            return False
    
    def ensure_vpc(
        self,
        region: str,
        name_prefix: str = "deploy-api",
        ip_range: str = None,
    ) -> str:
        """Ensure a VPC exists for the given region."""
        vpc_name = f"{name_prefix}-{region}"
        
        vpcs = self.list_vpcs()
        for vpc in vpcs:
            if vpc.get("name") == vpc_name and vpc.get("region") == region:
                return vpc["id"]
        
        if not ip_range:
            region_offsets = {
                "lon1": 0, "ams3": 1, "fra1": 2, "blr1": 3,
                "nyc1": 4, "nyc3": 5, "sfo3": 6, "tor1": 7,
                "sgp1": 8, "syd1": 9,
            }
            offset = region_offsets.get(region, hash(region) % 16)
            ip_range = f"10.{120 + offset}.0.0/20"
        
        try:
            vpc = self.create_vpc(
                name=vpc_name,
                region=region,
                ip_range=ip_range,
                description=f"Private network for {name_prefix} deployments",
            )
            return vpc["id"]
        except DOError as e:
            if "overlaps" in str(e).lower():
                vpcs = self.list_vpcs()
                for vpc in vpcs:
                    if vpc.get("region") == region:
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
    # Firewalls
    # =========================================================================
    
    def create_firewall(
        self,
        name: str,
        droplet_ids: Optional[List[int]] = None,
        tags: Optional[List[str]] = None,
        inbound_rules: Optional[List[Dict]] = None,
        outbound_rules: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """Create firewall with default SSH/HTTP/HTTPS rules."""
        if inbound_rules is None:
            inbound_rules = [
                {"protocol": "tcp", "ports": "22", "sources": {"addresses": ["0.0.0.0/0"]}},
                {"protocol": "tcp", "ports": "80", "sources": {"addresses": ["0.0.0.0/0"]}},
                {"protocol": "tcp", "ports": "443", "sources": {"addresses": ["0.0.0.0/0"]}},
                {"protocol": "tcp", "ports": "all", "sources": {"addresses": ["10.0.0.0/8"]}},
            ]
        
        if outbound_rules is None:
            outbound_rules = [
                {"protocol": "tcp", "ports": "all", "destinations": {"addresses": ["0.0.0.0/0"]}},
                {"protocol": "udp", "ports": "all", "destinations": {"addresses": ["0.0.0.0/0"]}},
                {"protocol": "icmp", "destinations": {"addresses": ["0.0.0.0/0"]}},
            ]
        
        data = {"name": name, "inbound_rules": inbound_rules, "outbound_rules": outbound_rules}
        if droplet_ids:
            data["droplet_ids"] = droplet_ids
        if tags:
            data["tags"] = tags
        
        result = self._post("/firewalls", data)
        return result.get("firewall", {})
    
    # =========================================================================
    # Snapshots
    # =========================================================================
    
    def list_snapshots(
        self,
        resource_type: str = "droplet",
        page: int = 1,
        per_page: int = 100,
    ) -> List[Dict[str, Any]]:
        """List snapshots."""
        params = {"resource_type": resource_type, "page": page, "per_page": per_page}
        result = self._get("/snapshots", params=params)
        return result.get("snapshots", [])
    
    def get_snapshot(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        """Get snapshot by ID."""
        try:
            result = self._get(f"/snapshots/{snapshot_id}")
            return result.get("snapshot")
        except DOError as e:
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
        """Create snapshot from a droplet."""
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
        except DOError as e:
            return Result.fail(str(e))
    
    def transfer_snapshot(
        self,
        snapshot_id: str,
        region: str,
        wait: bool = True,
        wait_timeout: int = 600,
    ) -> Dict[str, Any]:
        """Transfer snapshot to another region."""
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
        exclude_regions: List[str] = None,
        wait: bool = False,
    ) -> Dict[str, Any]:
        """
        Transfer snapshot to all available regions.
        
        Args:
            snapshot_id: Snapshot to transfer
            exclude_regions: Regions to skip
            wait: If True, wait for each transfer to complete (slow!)
            
        Returns:
            Dict with snapshot_id, snapshot_name, already_in, transferring_to, actions
        """
        exclude_regions = exclude_regions or []
        
        # Get all regions
        regions = self.list_regions()
        available = [r["slug"] for r in regions if r.get("available", True)]
        
        # Get snapshot info
        snapshot = self.get_snapshot(snapshot_id)
        if not snapshot:
            return {
                "snapshot_id": snapshot_id,
                "snapshot_name": None,
                "already_in": [],
                "transferring_to": [],
                "actions": [],
                "error": "Snapshot not found",
            }
        
        current_regions = snapshot.get("regions", [])
        target_regions = [
            r for r in available 
            if r not in current_regions and r not in exclude_regions
        ]
        
        if not target_regions:
            return {
                "snapshot_id": snapshot_id,
                "snapshot_name": snapshot.get("name"),
                "already_in": current_regions,
                "transferring_to": [],
                "actions": [],
            }
        
        # Start transfers
        actions = []
        for region in target_regions:
            try:
                action = self.transfer_snapshot(snapshot_id, region, wait=wait)
                actions.append({
                    "region": region,
                    "action_id": action.get("id"),
                    "status": action.get("status", "in-progress"),
                })
            except Exception as e:
                actions.append({
                    "region": region,
                    "error": str(e),
                })
        
        return {
            "snapshot_id": snapshot_id,
            "snapshot_name": snapshot.get("name"),
            "already_in": current_regions,
            "transferring_to": target_regions,
            "actions": actions,
        }
    
    # =========================================================================
    # Actions
    # =========================================================================
    
    def get_action(self, action_id: int) -> Dict[str, Any]:
        """Get action status."""
        result = self._get(f"/actions/{action_id}")
        return result.get("action", {})
    
    def _wait_for_action(self, action_id: int, timeout: int = 600) -> Dict[str, Any]:
        """Wait for an action to complete."""
        start = time.time()
        
        while time.time() - start < timeout:
            result = self._get(f"/actions/{action_id}")
            action = result.get("action", {})
            
            status = action.get("status", "")
            if status == "completed":
                return action
            elif status == "errored":
                raise DOError(f"Action {action_id} failed")
            
            time.sleep(10)
        
        raise DOError(f"Action {action_id} did not complete in {timeout}s")
    
    # =========================================================================
    # Container Registry
    # =========================================================================
    
    def get_registry(self) -> Optional[Dict[str, Any]]:
        """Get container registry info."""
        try:
            result = self._get("/registry")
            return result.get("registry")
        except DOError as e:
            if e.status_code == 404:
                return None
            raise
    
    def create_registry(
        self,
        name: str,
        region: str = "fra1",
        subscription_tier: str = "starter",
    ) -> Dict[str, Any]:
        """Create container registry."""
        result = self._post("/registry", {
            "name": name,
            "region": region,
            "subscription_tier_slug": subscription_tier,
        })
        return result.get("registry", {})
    
    def get_registry_credentials(
        self,
        read_write: bool = True,
        expiry_seconds: int = 3600,
    ) -> Dict:
        """Get Docker credentials for registry login."""
        resp = self._get("/registry/docker-credentials", params={
            "read_write": str(read_write).lower(),
            "expiry_seconds": expiry_seconds,
        })
        return resp
    
    def get_registry_endpoint(self) -> Optional[str]:
        """Get the registry endpoint URL."""
        registry = self.get_registry()
        if not registry:
            return None
        return f"registry.digitalocean.com/{registry.get('name')}"


# =============================================================================
# Async Client
# =============================================================================

class AsyncDOClient(AsyncBaseCloudClient):
    """
    DigitalOcean API client (async).
    
    Usage:
        async with AsyncDOClient(api_token="xxx") as client:
            droplets = await client.list_droplets()
    """
    
    PROVIDER = "DigitalOcean"
    BASE_URL = "https://api.digitalocean.com/v2"
    DEFAULT_IMAGE = "ubuntu-24-04-x64"
    
    def __init__(
        self,
        api_token: str,
        default_ssh_keys: Optional[List[str]] = None,
        config: CloudClientConfig = None,
    ):
        super().__init__(api_token, config)
        self.default_ssh_keys = default_ssh_keys or []
    
    # =========================================================================
    # HTTP Helpers
    # =========================================================================
    
    async def _request(
        self,
        method: str,
        path: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
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
        
        if response.status_code == 204:
            return {}
        
        result = response.json() if response.body else {}
        
        if response.status_code >= 400:
            error_msg = result.get("message", str(result))
            raise DOError(error_msg, response.status_code, result)
        
        return result
    
    async def _get(self, path: str, params: Optional[Dict] = None) -> Dict:
        return await self._request("GET", path, params=params)
    
    async def _post(self, path: str, data: Dict) -> Dict:
        return await self._request("POST", path, data=data)
    
    async def _delete(self, path: str) -> Dict:
        return await self._request("DELETE", path)
    
    # =========================================================================
    # Account
    # =========================================================================
    
    async def get_account(self) -> Dict[str, Any]:
        """Get account info."""
        result = await self._get("/account")
        return result.get("account", {})
    
    # =========================================================================
    # Droplets
    # =========================================================================
    
    async def create_droplet(
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
        """Create a new droplet."""
        if not vpc_uuid and auto_vpc:
            vpc_uuid = await self.ensure_vpc(region=region)
        
        all_tags = list(tags or [])
        if MANAGED_TAG not in all_tags:
            all_tags.append(MANAGED_TAG)
        if project:
            all_tags.append(f"project:{project}")
        if environment:
            all_tags.append(f"env:{environment}")
        
        final_user_data = user_data
        if node_agent_api_key:
            api_key_script = f"""#!/bin/bash
mkdir -p /etc/node-agent
echo "{node_agent_api_key}" > /etc/node-agent/api-key
chmod 600 /etc/node-agent/api-key
systemctl restart node_agent 2>/dev/null || true
"""
            if user_data:
                if user_data.startswith("#!/"):
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
        
        result = await self._post("/droplets", data)
        droplet = Droplet.from_api(result["droplet"])
        
        if wait:
            droplet = await self._wait_for_droplet(droplet.id, wait_timeout)
        
        return droplet
    
    async def get_droplet(self, droplet_id: int) -> Optional[Droplet]:
        """Get droplet by ID."""
        try:
            result = await self._get(f"/droplets/{droplet_id}")
            return Droplet.from_api(result["droplet"])
        except DOError as e:
            if e.status_code == 404:
                return None
            raise
    
    async def list_droplets(
        self,
        tag: Optional[str] = None,
        project: Optional[str] = None,
        environment: Optional[str] = None,
        service: Optional[str] = None,
        include_unmanaged: bool = False,
        page: int = 1,
        per_page: int = 100,
    ) -> List[Droplet]:
        """List droplets."""
        params = {"page": page, "per_page": per_page}
        
        filter_tag = tag
        if project and not tag:
            filter_tag = f"project:{project}"
        elif environment and not tag and not project:
            filter_tag = f"env:{environment}"
        elif service and not tag and not project and not environment:
            filter_tag = f"service:{service}"
        elif not tag and not project and not environment and not service and not include_unmanaged:
            filter_tag = MANAGED_TAG
        
        if filter_tag:
            params["tag_name"] = filter_tag
        
        result = await self._get("/droplets", params=params)
        droplets = [Droplet.from_api(d) for d in result.get("droplets", [])]
        
        if project and environment:
            env_tag = f"env:{environment}"
            droplets = [d for d in droplets if env_tag in (d.tags or [])]
        
        if service and (project or environment or tag):
            service_tag = f"service:{service}"
            droplets = [d for d in droplets if service_tag in (d.tags or [])]
        
        if not include_unmanaged and tag and tag != MANAGED_TAG:
            droplets = [d for d in droplets if d.is_managed]
        
        droplets = [d for d in droplets if "snapshot-builder" not in (d.tags or [])]
        
        return droplets
    
    async def power_off_droplet(self, droplet_id: int, wait: bool = False, timeout: int = 120) -> None:
        """
        Power off a droplet.
        
        Required before creating a snapshot for a clean disk state.
        
        Args:
            droplet_id: Droplet ID to power off
            wait: If True, wait for power off to complete
            timeout: Max seconds to wait (only if wait=True)
        """
        result = await self._post(f"/droplets/{droplet_id}/actions", {"type": "power_off"})
        
        if wait and result.get("action", {}).get("id"):
            action_id = result["action"]["id"]
            await self._wait_for_action(action_id, timeout)
    
    async def delete_droplet(self, droplet_id: int, force: bool = False) -> Result:
        """Delete a droplet."""
        if not force:
            droplet = await self.get_droplet(droplet_id)
            if droplet is None:
                return Result.fail(f"Droplet {droplet_id} not found")
            
            is_builder = "snapshot-builder" in (droplet.tags or [])
            if not droplet.is_managed and not is_builder:
                return Result.fail(
                    f"Droplet {droplet_id} ({droplet.name}) is not managed. "
                    f"Use force=True to delete anyway."
                )
        
        try:
            await self._delete(f"/droplets/{droplet_id}")
            return Result.ok(f"Droplet {droplet_id} deleted")
        except DOError as e:
            return Result.fail(str(e))
    
    async def _wait_for_droplet(self, droplet_id: int, timeout: int = 300) -> Droplet:
        """Wait for droplet to become active."""
        start = time.time()
        
        while time.time() - start < timeout:
            droplet = await self.get_droplet(droplet_id)
            if droplet and droplet.is_active and droplet.ip:
                return droplet
            await asyncio.sleep(5)
        
        raise DOError(f"Droplet {droplet_id} did not become active in {timeout}s")
    
    # =========================================================================
    # SSH Keys
    # =========================================================================
    
    async def list_ssh_keys(self) -> List[Dict[str, Any]]:
        """List all SSH keys."""
        result = await self._get("/account/keys")
        return result.get("ssh_keys", [])
    
    async def add_ssh_key(self, name: str, public_key: str) -> Dict[str, Any]:
        """Add SSH key to account."""
        result = await self._post("/account/keys", {"name": name, "public_key": public_key})
        return result.get("ssh_key", {})
    
    # =========================================================================
    # VPC
    # =========================================================================
    
    async def list_vpcs(self) -> List[Dict[str, Any]]:
        """List all VPCs."""
        result = await self._get("/vpcs")
        return result.get("vpcs", [])
    
    async def create_vpc(
        self,
        name: str,
        region: str,
        ip_range: str = "10.120.0.0/20",
        description: str = "",
    ) -> Dict[str, Any]:
        """Create a new VPC."""
        data = {"name": name, "region": region, "ip_range": ip_range}
        if description:
            data["description"] = description
        result = await self._post("/vpcs", data)
        return result.get("vpc", {})
    
    async def ensure_vpc(
        self,
        region: str,
        name_prefix: str = "deploy-api",
        ip_range: str = None,
    ) -> str:
        """Ensure a VPC exists for the given region."""
        vpc_name = f"{name_prefix}-{region}"
        
        vpcs = await self.list_vpcs()
        for vpc in vpcs:
            if vpc.get("name") == vpc_name and vpc.get("region") == region:
                return vpc["id"]
        
        if not ip_range:
            region_offsets = {
                "lon1": 0, "ams3": 1, "fra1": 2, "blr1": 3,
                "nyc1": 4, "nyc3": 5, "sfo3": 6, "tor1": 7,
                "sgp1": 8, "syd1": 9,
            }
            offset = region_offsets.get(region, hash(region) % 16)
            ip_range = f"10.{120 + offset}.0.0/20"
        
        try:
            vpc = await self.create_vpc(
                name=vpc_name,
                region=region,
                ip_range=ip_range,
                description=f"Private network for {name_prefix} deployments",
            )
            return vpc["id"]
        except DOError as e:
            if "overlaps" in str(e).lower():
                vpcs = await self.list_vpcs()
                for vpc in vpcs:
                    if vpc.get("region") == region:
                        return vpc["id"]
            raise
    
    # =========================================================================
    # Regions & Sizes
    # =========================================================================
    
    async def list_regions(self) -> List[Dict[str, Any]]:
        """List available regions."""
        result = await self._get("/regions")
        return [r for r in result.get("regions", []) if r.get("available")]
    
    async def list_sizes(self) -> List[Dict[str, Any]]:
        """List available sizes."""
        result = await self._get("/sizes")
        return result.get("sizes", [])
    
    # =========================================================================
    # Snapshots
    # =========================================================================
    
    async def list_snapshots(
        self,
        resource_type: str = "droplet",
        page: int = 1,
        per_page: int = 100,
    ) -> List[Dict[str, Any]]:
        """List snapshots."""
        params = {"resource_type": resource_type, "page": page, "per_page": per_page}
        result = await self._get("/snapshots", params=params)
        return result.get("snapshots", [])
    
    async def get_snapshot(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        """Get snapshot by ID."""
        try:
            result = await self._get(f"/snapshots/{snapshot_id}")
            return result.get("snapshot")
        except DOError as e:
            if e.status_code == 404:
                return None
            raise
    
    async def get_snapshot_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get snapshot by name."""
        snapshots = await self.list_snapshots()
        for snap in snapshots:
            if snap.get("name") == name:
                return snap
        return None
    
    async def create_snapshot_from_droplet(
        self,
        droplet_id: int,
        name: str,
        wait: bool = True,
        wait_timeout: int = 600,
    ) -> Dict[str, Any]:
        """Create snapshot from a droplet."""
        result = await self._post(f"/droplets/{droplet_id}/actions", {
            "type": "snapshot",
            "name": name,
        })
        
        action = result.get("action", {})
        
        if wait and action.get("id"):
            action = await self._wait_for_action(action["id"], wait_timeout)
        
        return action
    
    async def delete_snapshot(self, snapshot_id: str) -> Result:
        """Delete a snapshot."""
        try:
            await self._delete(f"/snapshots/{snapshot_id}")
            return Result.ok(f"Snapshot {snapshot_id} deleted")
        except DOError as e:
            return Result.fail(str(e))
    
    async def transfer_snapshot(
        self,
        snapshot_id: str,
        region: str,
        wait: bool = True,
        wait_timeout: int = 600,
    ) -> Dict[str, Any]:
        """Transfer snapshot to another region."""
        result = await self._post(f"/images/{snapshot_id}/actions", {
            "type": "transfer",
            "region": region,
        })
        
        action = result.get("action", {})
        
        if wait and action.get("id"):
            action = await self._wait_for_action(action["id"], wait_timeout)
        
        return action
    
    async def transfer_snapshot_to_all_regions(
        self,
        snapshot_id: str,
        exclude_regions: List[str] = None,
        wait: bool = False,
    ) -> Dict[str, Any]:
        """
        Transfer snapshot to all available regions.
        
        Args:
            snapshot_id: Snapshot to transfer
            exclude_regions: Regions to skip
            wait: If True, wait for each transfer to complete (slow!)
            
        Returns:
            Dict with snapshot_id, snapshot_name, already_in, transferring_to, actions
        """
        exclude_regions = exclude_regions or []
        
        # Get all regions
        regions = await self.list_regions()
        available = [r["slug"] for r in regions if r.get("available", True)]
        
        # Get snapshot info
        snapshot = await self.get_snapshot(snapshot_id)
        if not snapshot:
            return {
                "snapshot_id": snapshot_id,
                "snapshot_name": None,
                "already_in": [],
                "transferring_to": [],
                "actions": [],
                "error": "Snapshot not found",
            }
        
        current_regions = snapshot.get("regions", [])
        target_regions = [
            r for r in available 
            if r not in current_regions and r not in exclude_regions
        ]
        
        if not target_regions:
            return {
                "snapshot_id": snapshot_id,
                "snapshot_name": snapshot.get("name"),
                "already_in": current_regions,
                "transferring_to": [],
                "actions": [],
            }
        
        # Start transfers
        actions = []
        for region in target_regions:
            try:
                action = await self.transfer_snapshot(snapshot_id, region, wait=wait)
                actions.append({
                    "region": region,
                    "action_id": action.get("id"),
                    "status": action.get("status", "in-progress"),
                })
            except Exception as e:
                actions.append({
                    "region": region,
                    "error": str(e),
                })
        
        return {
            "snapshot_id": snapshot_id,
            "snapshot_name": snapshot.get("name"),
            "already_in": current_regions,
            "transferring_to": target_regions,
            "actions": actions,
        }
    
    # =========================================================================
    # Actions
    # =========================================================================
    
    async def get_action(self, action_id: int) -> Dict[str, Any]:
        """Get action status."""
        result = await self._get(f"/actions/{action_id}")
        return result.get("action", {})
    
    async def _wait_for_action(self, action_id: int, timeout: int = 600) -> Dict[str, Any]:
        """Wait for an action to complete."""
        start = time.time()
        
        while time.time() - start < timeout:
            result = await self._get(f"/actions/{action_id}")
            action = result.get("action", {})
            
            status = action.get("status", "")
            if status == "completed":
                return action
            elif status == "errored":
                raise DOError(f"Action {action_id} failed")
            
            await asyncio.sleep(10)
        
        raise DOError(f"Action {action_id} did not complete in {timeout}s")
    
    # =========================================================================
    # Container Registry
    # =========================================================================
    
    async def get_registry(self) -> Optional[Dict[str, Any]]:
        """Get container registry info."""
        try:
            result = await self._get("/registry")
            return result.get("registry")
        except DOError as e:
            if e.status_code == 404:
                return None
            raise
    
    async def create_registry(
        self,
        name: str,
        region: str = "fra1",
        subscription_tier: str = "starter",
    ) -> Dict[str, Any]:
        """Create container registry."""
        result = await self._post("/registry", {
            "name": name,
            "region": region,
            "subscription_tier_slug": subscription_tier,
        })
        return result.get("registry", {})
    
    async def get_registry_credentials(
        self,
        read_write: bool = True,
        expiry_seconds: int = 3600,
    ) -> Dict:
        """Get Docker credentials for registry login."""
        resp = await self._get("/registry/docker-credentials", params={
            "read_write": str(read_write).lower(),
            "expiry_seconds": expiry_seconds,
        })
        return resp
    
    async def get_registry_endpoint(self) -> Optional[str]:
        """Get the registry endpoint URL."""
        registry = await self.get_registry()
        if not registry:
            return None
        return f"registry.digitalocean.com/{registry.get('name')}"


# Backwards compatibility alias
DOAPIError = DOError
