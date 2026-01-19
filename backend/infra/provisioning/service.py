"""
Provisioning Service - Server provisioning logic.

Usage (Sync - CLI/scripts):
    from infra.provisioning import ProvisioningService
    
    service = ProvisioningService(do_token, user_id)
    result = service.provision_server(region="lon1", size="s-1vcpu-1gb", snapshot_id="123")
    
    # With streaming progress
    for event in service.provision_with_progress(region="lon1", size="s-1vcpu-1gb"):
        print(event.message)

Usage (Async - FastAPI):
    from infra.provisioning import AsyncProvisioningService
    
    service = AsyncProvisioningService(do_token, user_id)
    result = await service.provision_server(region="lon1", ...)
    
    # With streaming progress
    async for event in service.provision_with_progress(region="lon1", ...):
        print(event.message)
"""

from __future__ import annotations
import asyncio
from typing import List, Optional, Dict, Any, Generator, AsyncGenerator

from .models import ProvisionRequest, ProvisionResult, ProvisionProgress


class _BaseProvisioningService:
    """Base class with shared provisioning logic."""
    
    MANAGED_TAG = "deployed-via-api"
    AGENT_WAIT_TIMEOUT = 60  # seconds
    AGENT_CHECK_INTERVAL = 5  # seconds
    
    def __init__(self, do_token: str, user_id: str):
        self.do_token = do_token
        self.user_id = user_id
        
        # Generate agent API key
        from ..providers import generate_node_agent_key
        self.api_key = generate_node_agent_key(do_token)
    
    def _validate_tags(self, tags: List[str]) -> List[str]:
        """Ensure managed tag is present."""
        result = list(tags) if tags else []
        if self.MANAGED_TAG not in result:
            result.append(self.MANAGED_TAG)
        return result
    
    def _validate_snapshot_in_region(
        self,
        snapshot_id: str,
        region: str,
        snapshots: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Validate snapshot exists and is in target region."""
        snapshot = None
        for s in snapshots:
            if str(s.get("id")) == str(snapshot_id):
                snapshot = s
                break
        
        if not snapshot:
            raise ValueError(f"Snapshot '{snapshot_id}' not found")
        
        available_regions = snapshot.get("regions", [])
        if region not in available_regions:
            raise ValueError(
                f"Snapshot '{snapshot.get('name')}' not available in region '{region}'. "
                f"Available: {', '.join(available_regions)}"
            )
        
        return snapshot
    
    def _find_base_snapshot(self, snapshots: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Find the most recent base snapshot."""
        base_snaps = [s for s in snapshots if s.get("name", "").startswith("base-")]
        if base_snaps:
            # Sort by created_at descending to get most recent
            base_snaps.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return base_snaps[0]
        return None


class ProvisioningService(_BaseProvisioningService):
    """Synchronous provisioning service for CLI and scripts."""
    
    def provision_server(
        self,
        region: str,
        size: str = "s-1vcpu-1gb",
        snapshot_id: Optional[str] = None,
        name: Optional[str] = None,
        tags: Optional[List[str]] = None,
        ssh_keys: Optional[List[str]] = None,
        vpc_uuid: Optional[str] = None,
        project: Optional[str] = None,
        environment: str = "prod",
    ) -> ProvisionResult:
        """Provision a new server (sync)."""
        from ..providers import DOClient, SnapshotService
        from ..utils import generate_friendly_name
        
        client = DOClient(self.do_token)
        
        try:
            server_name = name.strip() if name else generate_friendly_name()
            final_tags = self._validate_tags(tags)
            final_ssh_keys = list(ssh_keys) if ssh_keys else []
            
            # Validate snapshot if provided
            if snapshot_id:
                snapshot_service = SnapshotService(self.do_token)
                snapshots = snapshot_service.list_snapshots()
                self._validate_snapshot_in_region(snapshot_id, region, snapshots)
                image = snapshot_id
            else:
                image = "ubuntu-24-04-x64"
            
            droplet = client.create_droplet(
                name=server_name,
                region=region,
                size=size,
                image=image,
                ssh_keys=final_ssh_keys,
                tags=final_tags,
                vpc_uuid=vpc_uuid,
                project=project,
                environment=environment,
                node_agent_api_key=self.api_key,
                wait=True,
            )
            
            return ProvisionResult(
                success=True,
                server=droplet.to_dict(),
                vpc_uuid=droplet.vpc_uuid,
            )
            
        except ValueError as e:
            return ProvisionResult(success=False, error=str(e))
        except Exception as e:
            error_msg = str(e)
            if "not available in the selected region" in error_msg:
                return ProvisionResult(
                    success=False,
                    error=f"Image not available in region '{region}'. Try different region."
                )
            return ProvisionResult(success=False, error=error_msg)
    
    def delete_server(self, server_id: int) -> ProvisionResult:
        """Delete a server (sync)."""
        from ..providers import DOClient
        
        client = DOClient(self.do_token)
        try:
            result = client.delete_droplet(server_id)
            if result.success:
                return ProvisionResult(success=True)
            return ProvisionResult(success=False, error=result.error)
        except Exception as e:
            return ProvisionResult(success=False, error=str(e))
    
    def list_servers(self) -> List[Dict[str, Any]]:
        """List all managed servers (sync)."""
        from ..providers import DOClient
        
        client = DOClient(self.do_token)
        droplets = client.list_droplets()
        return [d.to_dict() for d in droplets]
    
    def provision_with_progress(
        self,
        region: str,
        size: str = "s-1vcpu-1gb",
        snapshot_id: Optional[str] = None,
        name: Optional[str] = None,
        tags: Optional[List[str]] = None,
        ssh_keys: Optional[List[str]] = None,
        wait_for_agent: bool = True,
    ) -> Generator[ProvisionProgress, None, None]:
        """
        Provision a server with streaming progress events.
        
        Yields ProvisionProgress events that can be converted to SSE.
        
        Usage:
            for event in service.provision_with_progress(region="lon1"):
                print(event.message)
                if event.type == "complete":
                    print(f"Server ready at {event.ip}")
        """
        import time
        from ..providers import DOClient, SnapshotService
        from ..node_agent import NodeAgentClient
        from ..utils import generate_friendly_name
        
        def emit(msg_type: str, message: str, **kwargs) -> ProvisionProgress:
            return ProvisionProgress(type=msg_type, message=message, **kwargs)
        
        try:
            server_name = name.strip() if name else generate_friendly_name()
            yield emit("progress", f"Starting provisioning of {server_name}...")
            yield emit("progress", f"Region: {region}, Size: {size}")
            
            # Find snapshot if not provided
            actual_snapshot_id = snapshot_id
            if not actual_snapshot_id:
                yield emit("progress", "Looking for base snapshot...")
                snap_service = SnapshotService(self.do_token)
                snapshots = snap_service.list_snapshots()
                base_snap = self._find_base_snapshot(snapshots)
                if base_snap:
                    actual_snapshot_id = base_snap["id"]
                    yield emit("progress", f"Using snapshot: {base_snap['name']}")
                else:
                    yield emit("error", "No base snapshot found. Create one first.")
                    yield emit("complete", "Provisioning failed", success=False)
                    return
            
            # Provision the server
            yield emit("progress", "Creating droplet...")
            result = self.provision_server(
                region=region,
                size=size,
                snapshot_id=actual_snapshot_id,
                name=server_name,
                tags=tags,
                ssh_keys=ssh_keys,
            )
            
            if not result.success:
                yield emit("error", result.error or "Provisioning failed")
                yield emit("complete", "Provisioning failed", success=False)
                return
            
            yield emit("progress", f"Droplet created: {result.droplet_id}")
            
            if result.ip:
                yield emit("progress", f"IP assigned: {result.ip}")
            else:
                yield emit("progress", "Waiting for IP address...")
            
            # Wait for agent if requested
            if wait_for_agent and result.ip:
                yield emit("progress", "Waiting for server to boot...")
                time.sleep(5)
                
                yield emit("progress", "Verifying agent connectivity...")
                client = NodeAgentClient(result.ip, self.do_token)
                
                max_checks = self.AGENT_WAIT_TIMEOUT // self.AGENT_CHECK_INTERVAL
                for i in range(max_checks):
                    try:
                        health = client.health_check_sync()
                        if health.success:
                            yield emit("progress", "✅ Agent responding!")
                            break
                    except Exception:
                        pass
                    elapsed = (i + 1) * self.AGENT_CHECK_INTERVAL
                    yield emit("progress", f"Waiting for agent... ({elapsed}s)")
                    time.sleep(self.AGENT_CHECK_INTERVAL)
            
            yield emit(
                "complete",
                f"Server {server_name} provisioned successfully!",
                success=True,
                ip=result.ip,
                droplet_id=result.droplet_id,
                server_name=server_name,
            )
            
        except Exception as e:
            yield emit("error", str(e))
            yield emit("complete", f"Error: {e}", success=False)


class AsyncProvisioningService(_BaseProvisioningService):
    """Asynchronous provisioning service for FastAPI."""
    
    async def provision_server(
        self,
        region: str,
        size: str = "s-1vcpu-1gb",
        snapshot_id: Optional[str] = None,
        name: Optional[str] = None,
        tags: Optional[List[str]] = None,
        ssh_keys: Optional[List[str]] = None,
        vpc_uuid: Optional[str] = None,
        project: Optional[str] = None,
        environment: str = "prod",
    ) -> ProvisionResult:
        """Provision a new server (async)."""
        from ..providers import AsyncDOClient, AsyncSnapshotService
        from ..utils import generate_friendly_name
        
        client = AsyncDOClient(self.do_token)
        
        try:
            server_name = name.strip() if name else generate_friendly_name()
            final_tags = self._validate_tags(tags)
            final_ssh_keys = list(ssh_keys) if ssh_keys else []
            
            # Validate snapshot if provided
            if snapshot_id:
                snapshot_service = AsyncSnapshotService(self.do_token)
                try:
                    snapshots = await snapshot_service.list_snapshots()
                finally:
                    await snapshot_service.close()
                self._validate_snapshot_in_region(snapshot_id, region, snapshots)
                image = snapshot_id
            else:
                image = "ubuntu-24-04-x64"
            
            droplet = await client.create_droplet(
                name=server_name,
                region=region,
                size=size,
                image=image,
                ssh_keys=final_ssh_keys,
                tags=final_tags,
                vpc_uuid=vpc_uuid,
                project=project,
                environment=environment,
                node_agent_api_key=self.api_key,
                wait=True,
            )
            
            return ProvisionResult(
                success=True,
                server=droplet.to_dict(),
                vpc_uuid=droplet.vpc_uuid,
            )
            
        except ValueError as e:
            return ProvisionResult(success=False, error=str(e))
        except Exception as e:
            error_msg = str(e)
            if "not available in the selected region" in error_msg:
                return ProvisionResult(
                    success=False,
                    error=f"Image not available in region '{region}'. Try different region."
                )
            return ProvisionResult(success=False, error=error_msg)
        finally:
            await client.close()
    
    async def delete_server(self, server_id: int) -> ProvisionResult:
        """Delete a server (async)."""
        from ..providers import AsyncDOClient
        
        client = AsyncDOClient(self.do_token)
        try:
            result = await client.delete_droplet(server_id)
            if result.success:
                return ProvisionResult(success=True)
            return ProvisionResult(success=False, error=result.error)
        except Exception as e:
            return ProvisionResult(success=False, error=str(e))
        finally:
            await client.close()
    
    async def list_servers(self) -> List[Dict[str, Any]]:
        """List all managed servers (async)."""
        from ..providers import AsyncDOClient
        
        client = AsyncDOClient(self.do_token)
        try:
            droplets = await client.list_droplets()
            return [d.to_dict() for d in droplets]
        finally:
            await client.close()
    
    async def provision_with_progress(
        self,
        region: str,
        size: str = "s-1vcpu-1gb",
        snapshot_id: Optional[str] = None,
        name: Optional[str] = None,
        tags: Optional[List[str]] = None,
        ssh_keys: Optional[List[str]] = None,
        wait_for_agent: bool = True,
    ) -> AsyncGenerator[ProvisionProgress, None]:
        """
        Provision a server with streaming progress events (async).
        
        Yields ProvisionProgress events that can be converted to SSE.
        
        Usage:
            async for event in service.provision_with_progress(region="lon1"):
                print(event.message)
                if event.type == "complete":
                    print(f"Server ready at {event.ip}")
        """
        from ..providers import SnapshotService
        from ..node_agent import NodeAgentClient
        from ..utils import generate_friendly_name
        
        def emit(msg_type: str, message: str, **kwargs) -> ProvisionProgress:
            return ProvisionProgress(type=msg_type, message=message, **kwargs)
        
        try:
            server_name = name.strip() if name else generate_friendly_name()
            yield emit("progress", f"Starting provisioning of {server_name}...")
            yield emit("progress", f"Region: {region}, Size: {size}")
            
            # Find snapshot if not provided
            actual_snapshot_id = snapshot_id
            if not actual_snapshot_id:
                yield emit("progress", "Looking for base snapshot...")
                snap_service = SnapshotService(self.do_token)
                snapshots = snap_service.list_snapshots()
                base_snap = self._find_base_snapshot(snapshots)
                if base_snap:
                    actual_snapshot_id = base_snap["id"]
                    yield emit("progress", f"Using snapshot: {base_snap['name']}")
                else:
                    yield emit("error", "No base snapshot found. Create one first.")
                    yield emit("complete", "Provisioning failed", success=False)
                    return
            
            # Provision the server
            yield emit("progress", "Creating droplet...")
            result = await self.provision_server(
                region=region,
                size=size,
                snapshot_id=actual_snapshot_id,
                name=server_name,
                tags=tags,
                ssh_keys=ssh_keys,
            )
            
            if not result.success:
                yield emit("error", result.error or "Provisioning failed")
                yield emit("complete", "Provisioning failed", success=False)
                return
            
            yield emit("progress", f"Droplet created: {result.droplet_id}")
            
            if result.ip:
                yield emit("progress", f"IP assigned: {result.ip}")
            else:
                yield emit("progress", "Waiting for IP address...")
            
            # Wait for agent if requested
            if wait_for_agent and result.ip:
                yield emit("progress", "Waiting for server to boot...")
                await asyncio.sleep(5)
                
                yield emit("progress", "Verifying agent connectivity...")
                client = NodeAgentClient(result.ip, self.do_token)
                
                max_checks = self.AGENT_WAIT_TIMEOUT // self.AGENT_CHECK_INTERVAL
                for i in range(max_checks):
                    try:
                        health = await client.health_check()
                        if health.success:
                            yield emit("progress", "✅ Agent responding!")
                            break
                    except Exception:
                        pass
                    elapsed = (i + 1) * self.AGENT_CHECK_INTERVAL
                    yield emit("progress", f"Waiting for agent... ({elapsed}s)")
                    await asyncio.sleep(self.AGENT_CHECK_INTERVAL)
            
            yield emit(
                "complete",
                f"Server {server_name} provisioned successfully!",
                success=True,
                ip=result.ip,
                droplet_id=result.droplet_id,
                server_name=server_name,
            )
            
        except Exception as e:
            yield emit("error", str(e))
            yield emit("complete", f"Error: {e}", success=False)
