"""
DigitalOcean Client for Infrastructure Module.

Extends shared cloud.DOClient with infra-specific methods.
Provides both sync and async variants.

Usage:
    # Sync (for scripts)
    client = DOClient(token)
    droplet = client.create_droplet(...)
    
    # Async (for FastAPI)
    client = AsyncDOClient(token)
    droplet = await client.create_droplet(...)
"""

from __future__ import annotations
import time
import asyncio
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, Any, List, Optional

if TYPE_CHECKING:
    from ...context import DeploymentContext

# Import from shared cloud module (relative import)
from ....cloud.digitalocean import (
    DOClient as _BaseDOClient,
    AsyncDOClient as _BaseAsyncDOClient,
    Droplet,
    Result,
    DOError,
)

from ...core.result import Result as InfraResult, ServerResult

# Compatibility alias
DOAPIError = DOError

# Safety tag to identify managed droplets
MANAGED_TAG = "deployed-via-api"


class DOClient(_BaseDOClient):
    """
    DigitalOcean client with infra-specific extensions (sync).
    
    Extends shared cloud.DOClient with:
    - ensure_docker_snapshot() - Create Docker-ready snapshots
    - untag_droplet() - Remove tags from droplets
    - transfer_snapshot_to_all_regions() - Multi-region snapshot distribution
    - Registry management helpers
    """
    
    DOCKER_SNAPSHOT_NAME = "docker-ready-ubuntu-24"
    DEPLOYER_KEY_PATH = Path.home() / ".ssh" / "id_ed25519"
    
    # =========================================================================
    # Infra-specific: Tagging
    # =========================================================================
    
    def untag_droplet(self, droplet_id: int, tag: str) -> Result:
        """
        Remove a tag from a droplet.
        
        Args:
            droplet_id: Droplet ID
            tag: Tag to remove
            
        Returns:
            Result indicating success or failure
        """
        try:
            response = self._request(
                "DELETE",
                f"/tags/{tag}/resources",
                data={"resources": [{"resource_id": str(droplet_id), "resource_type": "droplet"}]},
            )
            return Result.ok(f"Removed tag '{tag}' from droplet {droplet_id}")
        except Exception as e:
            return Result.fail(f"Failed to untag droplet: {e}")
    
    # =========================================================================
    # Infra-specific: VPC
    # =========================================================================
    
    def get_vpc_members(self, vpc_id: str) -> List[Dict[str, Any]]:
        """Get all members (droplets) in a VPC."""
        result = self._get(f"/vpcs/{vpc_id}/members")
        return result.get("members", [])
    
    # =========================================================================
    # Infra-specific: Domain Records
    # =========================================================================
    
    def create_domain_record(
        self,
        domain: str,
        record_type: str,
        name: str,
        data: str,
        ttl: int = 1800,
        priority: int = None,
    ) -> Dict[str, Any]:
        """Create a DNS record for a domain."""
        payload = {
            "type": record_type,
            "name": name,
            "data": data,
            "ttl": ttl,
        }
        if priority is not None:
            payload["priority"] = priority
        
        result = self._post(f"/domains/{domain}/records", payload)
        return result.get("domain_record", {})
    
    def list_domain_records(self, domain: str) -> List[Dict[str, Any]]:
        """List all DNS records for a domain."""
        result = self._get(f"/domains/{domain}/records")
        return result.get("domain_records", [])
    
    # =========================================================================
    # Infra-specific: Snapshot Management
    # =========================================================================
    
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
            wait: If True, wait for transfers to complete (slow!)
            
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
        3. Pulls common images
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
        
        # Create temporary droplet with Docker install script
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
            tags=["snapshot-builder", "temporary", MANAGED_TAG],
            wait=True,
        )
        
        # Wait for cloud-init to complete
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
            raise DOError("Docker setup did not complete in time")
        
        # Power off before snapshot
        self._post(f"/droplets/{droplet.id}/actions", {"type": "power_off"})
        time.sleep(10)
        
        # Create snapshot
        self.create_snapshot_from_droplet(
            droplet.id,
            self.DOCKER_SNAPSHOT_NAME,
            wait=True,
        )
        
        # Delete temp droplet
        self.delete_droplet(droplet.id)
        
        # Return snapshot ID
        snapshot = self.get_snapshot_by_name(self.DOCKER_SNAPSHOT_NAME)
        if snapshot:
            return str(snapshot["id"])
        
        raise DOError("Failed to find created snapshot")
    
    # =========================================================================
    # Infra-specific: Registry Management
    # =========================================================================
    
    def ensure_registry(self, name: str = None, region: str = "fra1") -> Dict[str, Any]:
        """Ensure container registry exists, create if needed."""
        existing = self.get_registry()
        if existing:
            return existing
        
        # Generate name from token hash if not provided
        if not name:
            import hashlib
            name = "reg-" + hashlib.sha256(self._api_token.encode()).hexdigest()[:12]
        
        return self.create_registry(name, region)
    
    def list_registry_repositories(self) -> List[Dict[str, Any]]:
        """List all repositories in the registry."""
        registry = self.get_registry()
        if not registry:
            return []
        
        result = self._get(f"/registry/{registry['name']}/repositories")
        return result.get("repositories", [])
    
    def list_repository_tags(self, repository: str) -> List[Dict[str, Any]]:
        """List all tags for a repository."""
        registry = self.get_registry()
        if not registry:
            return []
        
        result = self._get(f"/registry/{registry['name']}/repositories/{repository}/tags")
        return result.get("tags", [])
    
    def delete_repository_tag(self, repository: str, tag: str) -> bool:
        """Delete a specific tag from a repository."""
        registry = self.get_registry()
        if not registry:
            return False
        
        try:
            self._delete(f"/registry/{registry['name']}/repositories/{repository}/tags/{tag}")
            return True
        except Exception:
            return False
    
    @classmethod
    def get_deployer_key_path(cls) -> Path:
        """Get the path to the deployer SSH key."""
        return cls.DEPLOYER_KEY_PATH


class AsyncDOClient(_BaseAsyncDOClient):
    """
    DigitalOcean client with infra-specific extensions (async).
    
    Async variant of DOClient for use in FastAPI and other async contexts.
    """
    
    DOCKER_SNAPSHOT_NAME = "docker-ready-ubuntu-24"
    DEPLOYER_KEY_PATH = Path.home() / ".ssh" / "id_ed25519"
    
    # =========================================================================
    # Infra-specific: Tagging
    # =========================================================================
    
    async def untag_droplet(self, droplet_id: int, tag: str) -> Result:
        """Remove a tag from a droplet."""
        try:
            await self._request(
                "DELETE",
                f"/tags/{tag}/resources",
                data={"resources": [{"resource_id": str(droplet_id), "resource_type": "droplet"}]},
            )
            return Result.ok(f"Removed tag '{tag}' from droplet {droplet_id}")
        except Exception as e:
            return Result.fail(f"Failed to untag droplet: {e}")
    
    # =========================================================================
    # Infra-specific: VPC
    # =========================================================================
    
    async def get_vpc_members(self, vpc_id: str) -> List[Dict[str, Any]]:
        """Get all members (droplets) in a VPC."""
        result = await self._get(f"/vpcs/{vpc_id}/members")
        return result.get("members", [])
    
    # =========================================================================
    # Infra-specific: Domain Records
    # =========================================================================
    
    async def create_domain_record(
        self,
        domain: str,
        record_type: str,
        name: str,
        data: str,
        ttl: int = 1800,
        priority: int = None,
    ) -> Dict[str, Any]:
        """Create a DNS record for a domain."""
        payload = {
            "type": record_type,
            "name": name,
            "data": data,
            "ttl": ttl,
        }
        if priority is not None:
            payload["priority"] = priority
        
        result = await self._post(f"/domains/{domain}/records", payload)
        return result.get("domain_record", {})
    
    async def list_domain_records(self, domain: str) -> List[Dict[str, Any]]:
        """List all DNS records for a domain."""
        result = await self._get(f"/domains/{domain}/records")
        return result.get("domain_records", [])
    
    # =========================================================================
    # Infra-specific: Snapshot Management
    # =========================================================================
    
    async def transfer_snapshot_to_all_regions(
        self,
        snapshot_id: str,
        exclude_regions: List[str] = None,
        wait: bool = False,
    ) -> Dict[str, Any]:
        """Transfer snapshot to all available regions.
        
        Args:
            snapshot_id: Snapshot to transfer
            exclude_regions: Regions to skip
            wait: If True, wait for transfers to complete (slow!)
            
        Returns:
            Dict with snapshot_id, snapshot_name, already_in, transferring_to, actions
        """
        exclude_regions = exclude_regions or []
        
        # Get all available regions
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
    
    async def ensure_docker_snapshot(
        self,
        region: str = "lon1",
        size: str = "s-1vcpu-1gb",
    ) -> str:
        """
        Ensure a Docker-ready snapshot exists (async version).
        
        Note: SSH commands still run synchronously via subprocess,
        but HTTP calls are async.
        """
        # Check if snapshot already exists
        existing = await self.get_snapshot_by_name(self.DOCKER_SNAPSHOT_NAME)
        if existing:
            return str(existing["id"])
        
        user_data = '''#!/bin/bash
set -e
curl -fsSL https://get.docker.com | sh
systemctl enable docker
systemctl start docker
docker pull postgres:15-alpine
docker pull redis:7-alpine
docker pull nginx:alpine
apt-get clean
rm -rf /var/lib/apt/lists/*
touch /tmp/docker-setup-complete
'''
        
        temp_name = f"docker-snapshot-builder-{int(time.time())}"
        
        droplet = await self.create_droplet(
            name=temp_name,
            region=region,
            size=size,
            user_data=user_data,
            tags=["snapshot-builder", "temporary", MANAGED_TAG],
            wait=True,
        )
        
        # Wait for cloud-init (SSH check runs in thread pool to not block)
        ssh_key_path = self.DEPLOYER_KEY_PATH
        max_wait = 300
        start = time.time()
        
        def check_ssh():
            try:
                result = subprocess.run(
                    [
                        "ssh", "-i", str(ssh_key_path),
                        "-o", "StrictHostKeyChecking=no",
                        "-o", "UserKnownHostsFile=/dev/null",
                        "-o", "ConnectTimeout=10",
                        f"root@{droplet.ip}",
                        "test -f /tmp/docker-setup-complete && docker --version"
                    ],
                    capture_output=True, text=True, timeout=30,
                )
                return result.returncode == 0 and "Docker version" in result.stdout
            except Exception:
                return False
        
        loop = asyncio.get_event_loop()
        while time.time() - start < max_wait:
            ready = await loop.run_in_executor(None, check_ssh)
            if ready:
                break
            await asyncio.sleep(10)
        else:
            await self.delete_droplet(droplet.id)
            raise DOError("Docker setup did not complete in time")
        
        # Power off before snapshot
        await self._post(f"/droplets/{droplet.id}/actions", {"type": "power_off"})
        await asyncio.sleep(10)
        
        # Create snapshot
        await self.create_snapshot_from_droplet(
            droplet.id,
            self.DOCKER_SNAPSHOT_NAME,
            wait=True,
        )
        
        # Delete temp droplet
        await self.delete_droplet(droplet.id)
        
        # Return snapshot ID
        snapshot = await self.get_snapshot_by_name(self.DOCKER_SNAPSHOT_NAME)
        if snapshot:
            return str(snapshot["id"])
        
        raise DOError("Failed to find created snapshot")
    
    # =========================================================================
    # Infra-specific: Registry Management
    # =========================================================================
    
    async def ensure_registry(self, name: str = None, region: str = "fra1") -> Dict[str, Any]:
        """Ensure container registry exists, create if needed."""
        existing = await self.get_registry()
        if existing:
            return existing
        
        if not name:
            import hashlib
            name = "reg-" + hashlib.sha256(self._api_token.encode()).hexdigest()[:12]
        
        return await self.create_registry(name, region)
    
    async def list_registry_repositories(self) -> List[Dict[str, Any]]:
        """List all repositories in the registry."""
        registry = await self.get_registry()
        if not registry:
            return []
        
        result = await self._get(f"/registry/{registry['name']}/repositories")
        return result.get("repositories", [])
    
    async def list_repository_tags(self, repository: str) -> List[Dict[str, Any]]:
        """List all tags for a repository."""
        registry = await self.get_registry()
        if not registry:
            return []
        
        result = await self._get(f"/registry/{registry['name']}/repositories/{repository}/tags")
        return result.get("tags", [])
    
    async def delete_repository_tag(self, repository: str, tag: str) -> bool:
        """Delete a specific tag from a repository."""
        registry = await self.get_registry()
        if not registry:
            return False
        
        try:
            await self._delete(f"/registry/{registry['name']}/repositories/{repository}/tags/{tag}")
            return True
        except Exception:
            return False
    
    @classmethod
    def get_deployer_key_path(cls) -> Path:
        """Get the path to the deployer SSH key."""
        return cls.DEPLOYER_KEY_PATH


# =========================================================================
# Context-Aware Server Manager (Async)
# =========================================================================

class ServerManager:
    """
    Context-aware server manager (async).
    
    Wraps AsyncDOClient with deployment context for automatic tagging, 
    naming, and storage integration.
    
    Usage:
        manager = ServerManager(ctx, do_token="xxx")
        
        # Provision servers for a service
        servers = await manager.provision(
            service="api",
            count=3,
            size="s-2vcpu-4gb",
        )
        
        # List servers for project
        servers = await manager.list_servers()
    """
    
    def __init__(
        self, 
        ctx: 'DeploymentContext',
        do_token: Optional[str] = None,
    ):
        self.ctx = ctx
        self._do_token = do_token
        self._client: Optional[AsyncDOClient] = None
    
    async def _get_client(self) -> AsyncDOClient:
        """Get or create async DO client."""
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
            
            self._client = AsyncDOClient(token)
        
        return self._client
    
    def _make_tags(self, service: Optional[str] = None) -> List[str]:
        """Generate tags for a droplet."""
        from ...utils.naming import sanitize_for_dns
        
        tags = [
            f"user-{sanitize_for_dns(self.ctx.user_id)}",
            f"project-{sanitize_for_dns(self.ctx.project_name)}",
            f"env-{sanitize_for_dns(self.ctx.env)}",
            MANAGED_TAG,
        ]
        if service:
            tags.append(f"service-{sanitize_for_dns(service)}")
        return tags
    
    def _make_name(self, service: str, index: int) -> str:
        """Generate droplet name."""
        from ...utils.naming import sanitize_for_dns
        
        namespace = sanitize_for_dns(self.ctx.namespace)
        service = sanitize_for_dns(service)
        return f"{namespace}-{service}-{index}"
    
    async def provision(
        self,
        service: str,
        count: int = 1,
        size: str = "s-1vcpu-1gb",
        region: str = "lon1",
        snapshot_id: str = None,
        user_data: str = None,
    ) -> List[ServerResult]:
        """
        Provision servers for a service.
        
        Args:
            service: Service name
            count: Number of servers
            size: Droplet size
            region: Region
            snapshot_id: Snapshot to use (or latest Docker snapshot)
            user_data: Cloud-init script
            
        Returns:
            List of ServerResult with provisioned server info
        """
        client = await self._get_client()
        results = []
        
        # Get snapshot if not provided
        if not snapshot_id:
            snapshot = await client.get_snapshot_by_name(DOClient.DOCKER_SNAPSHOT_NAME)
            if snapshot:
                snapshot_id = str(snapshot["id"])
        
        tags = self._make_tags(service)
        
        for i in range(count):
            name = self._make_name(service, i + 1)
            
            try:
                droplet = await client.create_droplet(
                    name=name,
                    region=region,
                    size=size,
                    image=snapshot_id or "ubuntu-24-04-x64",
                    tags=tags,
                    user_data=user_data,
                    wait=True,
                )
                
                results.append(ServerResult(
                    success=True,
                    server_id=str(droplet.id),
                    server_ip=droplet.ip,
                    server_name=droplet.name,
                    message=f"Provisioned {name}",
                ))
            except Exception as e:
                results.append(ServerResult(
                    success=False,
                    message=f"Failed to provision {name}: {e}",
                ))
        
        return results
    
    async def list_servers(
        self,
        service: str = None,
    ) -> List[Droplet]:
        """
        List servers for current project.
        
        Args:
            service: Filter by service name
            
        Returns:
            List of Droplet objects
        """
        client = await self._get_client()
        
        # Filter by project tag
        from ...utils.naming import sanitize_for_dns
        project_tag = f"project-{sanitize_for_dns(self.ctx.project_name)}"
        
        droplets = await client.list_droplets(tag=project_tag)
        
        # Further filter by service if specified
        if service:
            service_tag = f"service-{sanitize_for_dns(service)}"
            droplets = [d for d in droplets if service_tag in (d.tags or [])]
        
        return droplets
    
    async def delete_server(self, droplet_id: int) -> Result:
        """Delete a server."""
        client = await self._get_client()
        return await client.delete_droplet(droplet_id)
