"""
Fleet Service - Server fleet health monitoring.

Usage (Sync - CLI/scripts):
    from infra.fleet import FleetService
    
    service = FleetService(do_token, user_id)
    health = service.get_fleet_health()
    print(f"Status: {health.status}")

Usage (Async - FastAPI):
    from infra.fleet import AsyncFleetService
    
    service = AsyncFleetService(do_token, user_id)
    health = await service.get_fleet_health()
"""

from __future__ import annotations
import asyncio
from typing import List, Optional, Dict, Any

from .models import ServerHealth, FleetHealth


class _BaseFleetService:
    """Base class with shared fleet logic."""
    
    def __init__(self, do_token: str, user_id: str):
        self.do_token = do_token
        self.user_id = user_id
        
        from ..providers import generate_node_agent_key
        self.api_key = generate_node_agent_key(do_token)
    
    def _calculate_summary(self, servers: List[ServerHealth]) -> Dict[str, Any]:
        """Calculate fleet summary from server health data."""
        total = len(servers)
        online = sum(1 for s in servers if s.status == "online")
        healthy = sum(1 for s in servers if s.status == "online" and s.health_status in ("healthy", "empty"))
        unhealthy = sum(1 for s in servers if s.status == "online" and s.health_status == "unhealthy")
        unreachable = sum(1 for s in servers if s.status == "unreachable")
        
        if unreachable == 0 and unhealthy == 0:
            status = "healthy"
        elif online > 0:
            status = "degraded"
        else:
            status = "down"
        
        return {
            "total": total,
            "online": online,
            "healthy": healthy,
            "unhealthy": unhealthy,
            "unreachable": unreachable,
            "status": status,
        }


class FleetService(_BaseFleetService):
    """Synchronous fleet service for CLI and scripts."""
    
    def list_servers(self) -> List[Any]:
        """List all managed servers."""
        from ..providers import DOClient, MANAGED_TAG
        
        client = DOClient(self.do_token)
        droplets = client.list_droplets()
        # Filter to only managed droplets
        return [d for d in droplets if MANAGED_TAG in (d.tags or [])]
    
    def delete_server(self, droplet_id: str, force: bool = False) -> Dict[str, Any]:
        """
        Delete a server.
        
        Args:
            droplet_id: ID of the droplet to delete
            force: If True, delete even if not managed
            
        Returns:
            Dict with success status
        """
        from ..providers import DOClient, MANAGED_TAG
        
        client = DOClient(self.do_token)
        try:
            # Safety check - only delete managed droplets unless forced
            if not force:
                droplet = client.get_droplet(int(droplet_id))
                if droplet and MANAGED_TAG not in (droplet.tags or []):
                    return {
                        "success": False,
                        "error": f"Droplet {droplet_id} is not managed. Use force=True to delete anyway."
                    }
            
            client.delete_droplet(int(droplet_id))
            return {"success": True, "deleted": droplet_id}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_fleet_health(self) -> FleetHealth:
        """Get health status of all servers in the fleet (sync)."""
        from ..providers import DOClient
        from ..node_agent import NodeAgentClient
        
        # Get all servers
        try:
            client = DOClient(self.do_token)
            droplets = client.list_droplets()
            server_infos = [{"ip": d.ip, "name": d.name, "region": d.region} for d in droplets if d.ip]
        except Exception as e:
            return FleetHealth(error=str(e), status="down")
        
        if not server_infos:
            return FleetHealth(status="healthy")
        
        # Check each server (sequential for sync)
        servers = []
        for info in server_infos:
            health = self._check_server_sync(info)
            servers.append(health)
        
        summary = self._calculate_summary(servers)
        
        return FleetHealth(
            servers=servers,
            total=summary["total"],
            online=summary["online"],
            healthy=summary["healthy"],
            unhealthy=summary["unhealthy"],
            unreachable=summary["unreachable"],
            status=summary["status"],
        )
    
    def _check_server_sync(self, server_info: Dict[str, Any]) -> ServerHealth:
        """Check health of a single server (sync)."""
        from ..node_agent import NodeAgentClient
        
        ip = server_info.get("ip")
        
        try:
            client = NodeAgentClient(ip, self.do_token, timeout=10)
            
            # Ping agent
            ping = client.ping_sync()
            if not ping.success:
                return ServerHealth(
                    ip=ip,
                    name=server_info.get("name"),
                    region=server_info.get("region"),
                    status="unreachable",
                    error="Agent not responding",
                )
            
            agent_version = ping.data.get("version", "unknown") if ping.data else "unknown"
            
            # Get container health
            result = client.check_containers_health_sync()
            if result.success:
                summary = result.data.get("summary", {})
                return ServerHealth(
                    ip=ip,
                    name=server_info.get("name"),
                    region=server_info.get("region"),
                    status="online",
                    agent_version=agent_version,
                    containers=summary.get("total", 0),
                    healthy=summary.get("healthy", 0),
                    unhealthy=summary.get("unhealthy", 0),
                    health_status=summary.get("status", "unknown"),
                )
            else:
                return ServerHealth(
                    ip=ip,
                    name=server_info.get("name"),
                    region=server_info.get("region"),
                    status="online",
                    agent_version=agent_version,
                    error=result.error,
                )
                
        except Exception as e:
            return ServerHealth(
                ip=ip,
                name=server_info.get("name"),
                region=server_info.get("region"),
                status="unreachable",
                error=str(e),
            )
    
    def check_servers_health(self, server_ips: List[str]) -> List[Dict[str, Any]]:
        """
        Check health of specific servers by IP.
        
        Args:
            server_ips: List of server IPs to check
            
        Returns:
            List of health check results
        """
        from ..node_agent import NodeAgentClient
        
        results = []
        for ip in server_ips:
            ip = ip.strip()
            try:
                client = NodeAgentClient(ip, self.do_token, timeout=10)
                health = client.health_check_sync()
                results.append({
                    "ip": ip,
                    "healthy": health.success,
                    "data": health.data,
                })
            except Exception as e:
                results.append({
                    "ip": ip,
                    "healthy": False,
                    "error": str(e),
                })
        return results
    
    def get_service_state(
        self,
        project: str,
        service: str,
        environment: str,
        server_ips: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get state of a service's containers across servers.
        
        Args:
            project: Project name
            service: Service name
            environment: Environment (prod, dev, etc)
            server_ips: Optional list of specific servers to check.
                       If None, checks all managed servers.
                       
        Returns:
            List of server results with matching containers
        """
        from ..node_agent import NodeAgentClient
        
        # Get servers to check
        if server_ips:
            servers = [{"ip": ip.strip()} for ip in server_ips]
        else:
            server_list = self.list_servers()
            servers = [{"ip": s.ip, "name": s.name} for s in server_list if s.ip]
        
        container_prefix = f"{project}_{environment}_{service}"
        results = []
        
        for server in servers:
            ip = server.get("ip")
            try:
                client = NodeAgentClient(ip, self.do_token, timeout=10)
                containers_result = client.list_containers_sync()
                if containers_result.success:
                    matching = [
                        c for c in (containers_result.data or [])
                        if c.get("name", "").startswith(container_prefix)
                    ]
                    results.append({
                        "server_ip": ip,
                        "server_name": server.get("name"),
                        "containers": matching,
                    })
                else:
                    results.append({
                        "server_ip": ip,
                        "error": containers_result.error,
                    })
            except Exception as e:
                results.append({
                    "server_ip": ip,
                    "error": str(e),
                })
        
        return results
    
    def cleanup_service_containers(
        self,
        project: str,
        service: str,
        environment: str,
        server_ips: List[str],
    ) -> List[Dict[str, Any]]:
        """
        Clean up containers for a service on specified servers.
        
        Args:
            project: Project name
            service: Service name
            environment: Environment
            server_ips: List of server IPs to clean up
            
        Returns:
            List of cleanup results per server
        """
        from ..node_agent import NodeAgentClient
        
        container_prefix = f"{project}_{environment}_{service}"
        results = []
        
        for ip in server_ips:
            ip = ip.strip()
            try:
                client = NodeAgentClient(ip, self.do_token, timeout=30)
                
                # List containers matching pattern
                containers_result = client.list_containers_sync()
                if not containers_result.success:
                    results.append({"ip": ip, "error": containers_result.error})
                    continue
                
                matching = [
                    c for c in (containers_result.data or [])
                    if c.get("name", "").startswith(container_prefix)
                ]
                
                # Stop and remove containers
                removed = []
                for c in matching:
                    container_name = c["name"]
                    client.stop_container_sync(container_name)
                    client.remove_container_sync(container_name)
                    removed.append(container_name)
                
                results.append({"ip": ip, "removed": removed})
            except Exception as e:
                results.append({"ip": ip, "error": str(e)})
        
        return results


class AsyncFleetService(_BaseFleetService):
    """Asynchronous fleet service for FastAPI."""
    
    async def list_servers(self) -> List[Any]:
        """List all managed servers."""
        from ..providers import AsyncDOClient, MANAGED_TAG
        
        client = AsyncDOClient(self.do_token)
        try:
            droplets = await client.list_droplets()
            # Filter to only managed droplets
            return [d for d in droplets if MANAGED_TAG in (d.tags or [])]
        finally:
            await client.close()
    
    async def delete_server(self, droplet_id: str, force: bool = False) -> Dict[str, Any]:
        """
        Delete a server.
        
        Args:
            droplet_id: ID of the droplet to delete
            force: If True, delete even if not managed
            
        Returns:
            Dict with success status
        """
        from ..providers import AsyncDOClient, MANAGED_TAG
        
        client = AsyncDOClient(self.do_token)
        try:
            # Safety check - only delete managed droplets unless forced
            if not force:
                droplet = await client.get_droplet(int(droplet_id))
                if droplet and MANAGED_TAG not in (droplet.tags or []):
                    return {
                        "success": False,
                        "error": f"Droplet {droplet_id} is not managed. Use force=True to delete anyway."
                    }
            
            await client.delete_droplet(int(droplet_id))
            return {"success": True, "deleted": droplet_id}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            await client.close()
    
    async def get_fleet_health(self) -> FleetHealth:
        """Get health status of all servers in the fleet (async, parallel)."""
        from ..providers import AsyncDOClient
        
        # Get all servers
        try:
            client = AsyncDOClient(self.do_token)
            try:
                droplets = await client.list_droplets()
                server_infos = [{"ip": d.ip, "name": d.name, "region": d.region} for d in droplets if d.ip]
            finally:
                await client.close()
        except Exception as e:
            return FleetHealth(error=str(e), status="down")
        
        if not server_infos:
            return FleetHealth(status="healthy")
        
        # Check all servers in parallel
        tasks = [self._check_server_async(info) for info in server_infos]
        servers = await asyncio.gather(*tasks)
        
        summary = self._calculate_summary(servers)
        
        return FleetHealth(
            servers=servers,
            total=summary["total"],
            online=summary["online"],
            healthy=summary["healthy"],
            unhealthy=summary["unhealthy"],
            unreachable=summary["unreachable"],
            status=summary["status"],
        )
    
    async def _check_server_async(self, server_info: Dict[str, Any]) -> ServerHealth:
        """Check health of a single server (async)."""
        from ..node_agent import NodeAgentClient
        
        ip = server_info.get("ip")
        
        try:
            async with NodeAgentClient(ip, self.do_token, timeout=10) as client:
                # Ping agent
                ping = await client.ping()
                if not ping.success:
                    return ServerHealth(
                        ip=ip,
                        name=server_info.get("name"),
                        region=server_info.get("region"),
                        status="unreachable",
                        error="Agent not responding",
                    )
                
                agent_version = ping.data.get("version", "unknown") if ping.data else "unknown"
                
                # Get container health
                result = await client.check_containers_health()
                if result.success:
                    summary = result.data.get("summary", {})
                    return ServerHealth(
                        ip=ip,
                        name=server_info.get("name"),
                        region=server_info.get("region"),
                        status="online",
                        agent_version=agent_version,
                        containers=summary.get("total", 0),
                        healthy=summary.get("healthy", 0),
                        unhealthy=summary.get("unhealthy", 0),
                        health_status=summary.get("status", "unknown"),
                    )
                else:
                    return ServerHealth(
                        ip=ip,
                        name=server_info.get("name"),
                        region=server_info.get("region"),
                        status="online",
                        agent_version=agent_version,
                        error=result.error,
                    )
                    
        except Exception as e:
            return ServerHealth(
                ip=ip,
                name=server_info.get("name"),
                region=server_info.get("region"),
                status="unreachable",
                error=str(e),
            )
    
    async def check_servers_health(self, server_ips: List[str]) -> List[Dict[str, Any]]:
        """
        Check health of specific servers by IP (async, parallel).
        
        Args:
            server_ips: List of server IPs to check
            
        Returns:
            List of health check results
        """
        from ..node_agent import NodeAgentClient
        
        async def check_one(ip: str) -> Dict[str, Any]:
            ip = ip.strip()
            try:
                async with NodeAgentClient(ip, self.do_token, timeout=10) as client:
                    health = await client.health_check()
                    return {
                        "ip": ip,
                        "healthy": health.success,
                        "data": health.data,
                    }
            except Exception as e:
                return {
                    "ip": ip,
                    "healthy": False,
                    "error": str(e),
                }
        
        tasks = [check_one(ip) for ip in server_ips]
        return await asyncio.gather(*tasks)
    
    async def get_service_state(
        self,
        project: str,
        service: str,
        environment: str,
        server_ips: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get state of a service's containers across servers (async, parallel).
        
        Args:
            project: Project name
            service: Service name
            environment: Environment (prod, dev, etc)
            server_ips: Optional list of specific servers to check.
                       If None, checks all managed servers.
                       
        Returns:
            List of server results with matching containers
        """
        from ..node_agent import NodeAgentClient
        
        # Get servers to check
        if server_ips:
            servers = [{"ip": ip.strip()} for ip in server_ips]
        else:
            server_list = await self.list_servers()
            servers = [{"ip": s.ip, "name": s.name} for s in server_list if s.ip]
        
        container_prefix = f"{project}_{environment}_{service}"
        
        async def check_one(server: Dict[str, Any]) -> Dict[str, Any]:
            ip = server.get("ip")
            try:
                async with NodeAgentClient(ip, self.do_token, timeout=10) as client:
                    containers_result = await client.list_containers()
                    if containers_result.success:
                        matching = [
                            c for c in (containers_result.data or [])
                            if c.get("name", "").startswith(container_prefix)
                        ]
                        return {
                            "server_ip": ip,
                            "server_name": server.get("name"),
                            "containers": matching,
                        }
                    else:
                        return {
                            "server_ip": ip,
                            "error": containers_result.error,
                        }
            except Exception as e:
                return {
                    "server_ip": ip,
                    "error": str(e),
                }
        
        tasks = [check_one(s) for s in servers]
        return await asyncio.gather(*tasks)
    
    async def cleanup_service_containers(
        self,
        project: str,
        service: str,
        environment: str,
        server_ips: List[str],
    ) -> List[Dict[str, Any]]:
        """
        Clean up containers for a service on specified servers (async, parallel).
        
        Args:
            project: Project name
            service: Service name
            environment: Environment
            server_ips: List of server IPs to clean up
            
        Returns:
            List of cleanup results per server
        """
        from ..node_agent import NodeAgentClient
        
        container_prefix = f"{project}_{environment}_{service}"
        
        async def cleanup_one(ip: str) -> Dict[str, Any]:
            ip = ip.strip()
            try:
                async with NodeAgentClient(ip, self.do_token, timeout=30) as client:
                    # List containers matching pattern
                    containers_result = await client.list_containers()
                    if not containers_result.success:
                        return {"ip": ip, "error": containers_result.error}
                    
                    matching = [
                        c for c in (containers_result.data or [])
                        if c.get("name", "").startswith(container_prefix)
                    ]
                    
                    # Stop and remove containers
                    removed = []
                    for c in matching:
                        container_name = c["name"]
                        await client.stop_container(container_name)
                        await client.remove_container(container_name)
                        removed.append(container_name)
                    
                    return {"ip": ip, "removed": removed}
            except Exception as e:
                return {"ip": ip, "error": str(e)}
        
        tasks = [cleanup_one(ip) for ip in server_ips]
        return await asyncio.gather(*tasks)
