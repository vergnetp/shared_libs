"""
Architecture Service - Infrastructure topology discovery.

Discovers and maps the architecture of deployed services across servers.
Provides a graph representation of services, their dependencies, and infrastructure.

Usage (Sync - for CLI/scripts):
    from infra.architecture import ArchitectureService
    
    service = ArchitectureService(do_token, user_id)
    topology = service.get_topology()  # No async needed!
    
Usage (Async - for FastAPI):
    from infra.architecture import AsyncArchitectureService
    
    service = AsyncArchitectureService(do_token, user_id)
    topology = await service.get_topology()

CLI:
    python -m infra.architecture --token=xxx --user-id=yyy --json
"""

from __future__ import annotations
import asyncio
from typing import List, Optional, Set, Dict, Any
from abc import ABC, abstractmethod

from .models import (
    ArchitectureTopology,
    ServiceNode,
    ServiceEdge,
    ServerStatus,
    InfrastructureComponent,
    ServerInfo,
)


# Known stateful services for categorization
STATEFUL_SERVICES = {
    "postgres", "postgresql", "mysql", "mariadb", 
    "redis", "mongo", "mongodb", "opensearch", "elasticsearch"
}

# Infrastructure containers (not user-deployed services)
INFRA_CONTAINERS = {
    "nginx", "node-agent", "node_agent", "traefik", "caddy", "haproxy"
}


class _BaseArchitectureService(ABC):
    """
    Base class with shared logic for architecture discovery.
    
    Contains all parsing, categorization, and topology building logic.
    Subclasses implement the I/O (sync or async).
    """
    
    def __init__(
        self,
        do_token: str,
        user_id: str,
        domain_suffix: str = "digitalpixo.com",
    ):
        """
        Initialize architecture service.
        
        Args:
            do_token: DigitalOcean API token
            user_id: User ID for agent authentication
            domain_suffix: Domain suffix for generated domains
        """
        self.do_token = do_token
        self.user_id = user_id
        self.domain_suffix = domain_suffix
        
        # Generate agent API key
        from ..cloud import generate_node_agent_key
        self.api_key = generate_node_agent_key(do_token)
    
    # =========================================================================
    # Abstract methods - implemented by sync/async subclasses
    # =========================================================================
    
    @abstractmethod
    def _discover_servers(self) -> List[str]:
        """Discover all managed servers from DigitalOcean."""
        pass
    
    @abstractmethod
    def _fetch_server_data(self, ip: str) -> Dict[str, Any]:
        """Fetch containers and agent info from a single server."""
        pass
    
    @abstractmethod
    def _fetch_all_servers(self, server_ips: List[str]) -> List[Dict[str, Any]]:
        """Fetch data from all servers."""
        pass
    
    # =========================================================================
    # Shared logic - parsing and building (no I/O)
    # =========================================================================
    
    def _build_topology(
        self,
        server_results: List[Dict[str, Any]],
        project_filter: Optional[str] = None,
        env_filter: Optional[str] = None,
    ) -> ArchitectureTopology:
        """Build topology from server results."""
        nodes: List[ServiceNode] = []
        edges: List[ServiceEdge] = []
        servers: List[ServerStatus] = []
        infrastructure: List[InfrastructureComponent] = []
        
        seen_services: Set[str] = set()
        seen_infra: Set[str] = set()
        nodes_by_id: Dict[str, ServiceNode] = {}
        
        for server_data in server_results:
            ip = server_data["ip"]
            containers = server_data["containers"]
            
            # Handle error servers
            if server_data["status"] == "error":
                servers.append(ServerStatus(
                    ip=ip,
                    containers=0,
                    status="error",
                    error=server_data["error"],
                    nginx_status="unknown",
                    agent_version=server_data["agent_version"],
                ))
                continue
            
            server_container_count = 0
            server_nginx_status = "not running"
            
            for container in containers:
                # Docker JSON format uses capital letters
                name = container.get("Names", container.get("name", ""))
                state = container.get("State", container.get("status", ""))
                
                # Skip non-running containers
                if state.lower() != "running":
                    continue
                
                server_container_count += 1
                name_lower = name.lower()
                
                # Check if infrastructure container
                is_infra = any(infra in name_lower for infra in INFRA_CONTAINERS)
                
                if is_infra:
                    if "nginx" in name_lower:
                        server_nginx_status = "running"
                    
                    # Add to infrastructure list (dedupe)
                    infra_key = f"{name}@{ip}"
                    if infra_key not in seen_infra:
                        seen_infra.add(infra_key)
                        infrastructure.append(self._parse_infra_container(container, ip))
                    continue
                
                # Parse service container
                parsed = self._parse_container_name(name)
                workspace_id, project, env, service = parsed
                
                # Apply filters
                if project_filter and project != project_filter:
                    continue
                if env_filter and env != env_filter:
                    continue
                
                node_id = f"{project}_{env}_{service}"
                
                if node_id not in seen_services:
                    seen_services.add(node_id)
                    node = self._create_service_node(
                        container, node_id, workspace_id, project, env, service, ip, server_nginx_status
                    )
                    nodes.append(node)
                    nodes_by_id[node_id] = node
                else:
                    # Add server to existing node
                    self._add_server_to_node(nodes_by_id[node_id], container, ip, server_nginx_status)
            
            # Add server status
            servers.append(ServerStatus(
                ip=ip,
                containers=server_container_count,
                status="online",
                nginx_status=server_nginx_status,
                agent_version=server_data["agent_version"],
            ))
        
        # Infer edges (dependencies)
        edges = self._infer_edges(nodes)
        
        return ArchitectureTopology(
            nodes=nodes,
            edges=edges,
            servers=servers,
            infrastructure=infrastructure,
        )
    
    def _parse_container_name(self, name: str) -> tuple:
        """
        Parse container name into components.
        
        Expected format: {workspace}_{project}_{env}_{service}
        
        Returns:
            (workspace_id, project, env, service)
        """
        parts = name.split("_")
        if len(parts) >= 4:
            workspace_id = parts[0]
            project = parts[1]
            env = parts[2]
            service = "_".join(parts[3:])  # Handle services with underscores
        else:
            # Unknown naming convention
            workspace_id = "unknown"
            project = name
            env = "unknown"
            service = name
        
        return workspace_id, project, env, service
    
    def _parse_ports(self, container: Dict[str, Any]) -> tuple:
        """
        Parse port information from container.
        
        Returns:
            (port_list, host_port, container_port)
        """
        ports_str = container.get("Ports", container.get("ports", ""))
        port_info = []
        container_port = None
        host_port = None
        
        if ports_str:
            for port_mapping in str(ports_str).split(", "):
                if port_mapping.strip():
                    port_info.append(port_mapping.strip())
                    # Extract ports from "0.0.0.0:18466->8000/tcp"
                    if "->" in port_mapping:
                        try:
                            left, right = port_mapping.split("->")
                            host_port = int(left.split(":")[-1])
                            container_port = int(right.split("/")[0])
                        except:
                            pass
        
        return port_info, host_port, container_port
    
    def _get_node_type(self, service: str) -> str:
        """Determine node type based on service name."""
        service_lower = service.lower()
        if service_lower in STATEFUL_SERVICES:
            return "stateful"
        elif service_lower == "nginx":
            return "proxy"
        return "service"
    
    def _create_service_node(
        self,
        container: Dict[str, Any],
        node_id: str,
        workspace_id: str,
        project: str,
        env: str,
        service: str,
        ip: str,
        nginx_status: str,
    ) -> ServiceNode:
        """Create a ServiceNode from container data."""
        port_info, host_port, container_port = self._parse_ports(container)
        
        # Calculate internal port
        from ..networking.ports import DeploymentPortResolver
        internal_port = DeploymentPortResolver.get_internal_port(
            workspace_id, project, env, service
        )
        
        # Generate domain
        domain = f"{workspace_id}-{project}-{env}-{service}.{self.domain_suffix}".replace("_", "-")
        
        return ServiceNode(
            id=node_id,
            container_name=container.get("Names", container.get("name", "")),
            type=self._get_node_type(service),
            service=service,
            project=project,
            env=env,
            status="running",
            ports=port_info,
            container_port=container_port,
            host_port=host_port,
            internal_port=internal_port,
            domain=domain,
            servers=[ServerInfo(ip=ip, container_port=host_port, nginx_status=nginx_status)],
            image=container.get("Image", container.get("image", "")),
        )
    
    def _add_server_to_node(
        self,
        node: ServiceNode,
        container: Dict[str, Any],
        ip: str,
        nginx_status: str,
    ) -> None:
        """Add a server to an existing node."""
        existing_ips = [s.ip for s in node.servers]
        if ip not in existing_ips:
            _, host_port, _ = self._parse_ports(container)
            node.servers.append(ServerInfo(
                ip=ip,
                container_port=host_port,
                nginx_status=nginx_status,
            ))
    
    def _parse_infra_container(
        self,
        container: Dict[str, Any],
        ip: str,
    ) -> InfrastructureComponent:
        """Parse an infrastructure container."""
        name = container.get("Names", container.get("name", ""))
        name_lower = name.lower()
        
        port_info, _, _ = self._parse_ports(container)
        
        if "nginx" in name_lower:
            comp_type = "nginx"
        elif "agent" in name_lower:
            comp_type = "agent"
        else:
            comp_type = "proxy"
        
        return InfrastructureComponent(
            name=name,
            type=comp_type,
            server_ip=ip,
            status="running",
            ports=port_info,
            image=container.get("Image", container.get("image", "")),
        )
    
    def _infer_edges(self, nodes: List[ServiceNode]) -> List[ServiceEdge]:
        """
        Infer edges (dependencies) between services.
        
        Current heuristic: services depend on stateful services in same project/env.
        """
        edges = []
        
        for node in nodes:
            if node.type == "service":
                for other in nodes:
                    if (other.type == "stateful" and 
                        other.project == node.project and 
                        other.env == node.env):
                        edges.append(ServiceEdge(
                            from_node=node.id,
                            to_node=other.id,
                            type="depends_on",
                            label=other.service,
                        ))
        
        return edges
    
    def _extract_projects(self, topology: ArchitectureTopology) -> List[Dict[str, Any]]:
        """Extract unique project/env combinations from topology."""
        seen = {}
        for node in topology.nodes:
            key = (node.project, node.env)
            if key not in seen:
                seen[key] = {"project": node.project, "environment": node.env, "server_count": 0}
            seen[key]["server_count"] = max(seen[key]["server_count"], len(node.servers))
        return list(seen.values())


# =============================================================================
# Sync version (for CLI/scripts)
# =============================================================================

class ArchitectureService(_BaseArchitectureService):
    """
    Synchronous architecture service for CLI and scripts.
    
    Usage:
        service = ArchitectureService(do_token, user_id)
        topology = service.get_topology()
        
        for node in topology.nodes:
            print(f"{node.id}: {node.type}")
    """
    
    def get_topology(
        self,
        server_ips: Optional[List[str]] = None,
        project: Optional[str] = None,
        environment: Optional[str] = None,
    ) -> ArchitectureTopology:
        """
        Get architecture topology (sync).
        
        Args:
            server_ips: List of server IPs to query. If None, queries all managed servers.
            project: Filter by project name
            environment: Filter by environment (prod, staging, etc.)
            
        Returns:
            ArchitectureTopology with nodes, edges, servers, and infrastructure
        """
        # Get server IPs if not provided
        if not server_ips:
            server_ips = self._discover_servers()
        
        if not server_ips:
            return ArchitectureTopology(
                message="No servers found",
                filters={"project": project, "environment": environment},
            )
        
        # Fetch data from all servers
        server_results = self._fetch_all_servers(server_ips)
        
        # Process results into topology
        topology = self._build_topology(
            server_results,
            project_filter=project,
            env_filter=environment,
        )
        
        topology.filters = {"project": project, "environment": environment}
        return topology
    
    def get_projects(self) -> List[Dict[str, Any]]:
        """Get list of all projects/environments across servers (sync)."""
        topology = self.get_topology()
        return self._extract_projects(topology)
    
    def _discover_servers(self) -> List[str]:
        """Discover all managed servers from DigitalOcean (sync)."""
        from ..cloud import DOClient
        
        do_client = DOClient(self.do_token)
        droplets = do_client.list_droplets()
        return [d.ip for d in droplets if d.ip]
    
    def _fetch_all_servers(self, server_ips: List[str]) -> List[Dict[str, Any]]:
        """Fetch data from all servers (sync - sequential)."""
        return [self._fetch_server_data(ip) for ip in server_ips]
    
    def _fetch_server_data(self, ip: str) -> Dict[str, Any]:
        """Fetch containers and agent info from a single server (sync)."""
        from ..node_agent import NodeAgentClient
        
        try:
            client = NodeAgentClient(ip, self.do_token)
            
            # Sequential calls (sync)
            containers_result = client.list_containers_sync()
            ping_result = client.ping_sync()
            
            # Process containers result
            if not containers_result.success:
                return {
                    "ip": ip,
                    "containers": [],
                    "status": "error",
                    "error": containers_result.error or "Failed",
                    "agent_version": "unknown",
                }
            
            # Process ping result
            agent_version = "unknown"
            if ping_result.success:
                agent_version = ping_result.data.get("version", "unknown")
            
            return {
                "ip": ip,
                "containers": containers_result.data.get("containers", []),
                "status": "online",
                "error": None,
                "agent_version": agent_version,
            }
            
        except Exception as e:
            return {
                "ip": ip,
                "containers": [],
                "status": "error",
                "error": str(e),
                "agent_version": "unknown",
            }


# =============================================================================
# Async version (for FastAPI)
# =============================================================================

class AsyncArchitectureService(_BaseArchitectureService):
    """
    Asynchronous architecture service for FastAPI.
    
    Usage:
        service = AsyncArchitectureService(do_token, user_id)
        topology = await service.get_topology()
    """
    
    async def get_topology(
        self,
        server_ips: Optional[List[str]] = None,
        project: Optional[str] = None,
        environment: Optional[str] = None,
    ) -> ArchitectureTopology:
        """
        Get architecture topology (async).
        
        Args:
            server_ips: List of server IPs to query. If None, queries all managed servers.
            project: Filter by project name
            environment: Filter by environment (prod, staging, etc.)
            
        Returns:
            ArchitectureTopology with nodes, edges, servers, and infrastructure
        """
        # Get server IPs if not provided
        if not server_ips:
            server_ips = await self._discover_servers()
        
        if not server_ips:
            return ArchitectureTopology(
                message="No servers found",
                filters={"project": project, "environment": environment},
            )
        
        # Fetch data from all servers in parallel
        server_results = await self._fetch_all_servers(server_ips)
        
        # Process results into topology (sync - just in-memory)
        topology = self._build_topology(
            server_results,
            project_filter=project,
            env_filter=environment,
        )
        
        topology.filters = {"project": project, "environment": environment}
        return topology
    
    async def get_projects(self) -> List[Dict[str, Any]]:
        """Get list of all projects/environments across servers (async)."""
        topology = await self.get_topology()
        return self._extract_projects(topology)
    
    async def _discover_servers(self) -> List[str]:
        """Discover all managed servers from DigitalOcean (async)."""
        from ..cloud import AsyncDOClient
        
        do_client = AsyncDOClient(self.do_token)
        try:
            droplets = await do_client.list_droplets()
            return [d.ip for d in droplets if d.ip]
        finally:
            await do_client.close()
    
    async def _fetch_all_servers(self, server_ips: List[str]) -> List[Dict[str, Any]]:
        """Fetch data from all servers in parallel (async)."""
        tasks = [self._fetch_server_data(ip) for ip in server_ips]
        return await asyncio.gather(*tasks)
    
    async def _fetch_server_data(self, ip: str) -> Dict[str, Any]:
        """Fetch containers and agent info from a single server (async)."""
        from ..node_agent import NodeAgentClient
        
        try:
            client = NodeAgentClient(ip, self.do_token)
            
            # Parallel: get containers AND ping at the same time
            containers_task = client.list_containers()
            ping_task = client.ping()
            
            results = await asyncio.gather(containers_task, ping_task, return_exceptions=True)
            containers_result = results[0]
            ping_result = results[1]
            
            # Process containers result
            if isinstance(containers_result, Exception) or not containers_result.success:
                error_msg = str(containers_result) if isinstance(containers_result, Exception) else (containers_result.error or "Failed")
                return {
                    "ip": ip,
                    "containers": [],
                    "status": "error",
                    "error": error_msg,
                    "agent_version": "unknown",
                }
            
            # Process ping result
            agent_version = "unknown"
            if not isinstance(ping_result, Exception) and ping_result.success:
                agent_version = ping_result.data.get("version", "unknown")
            
            return {
                "ip": ip,
                "containers": containers_result.data.get("containers", []),
                "status": "online",
                "error": None,
                "agent_version": agent_version,
            }
            
        except Exception as e:
            return {
                "ip": ip,
                "containers": [],
                "status": "error",
                "error": str(e),
                "agent_version": "unknown",
            }


# =============================================================================
# CLI support
# =============================================================================

if __name__ == "__main__":
    import argparse
    import json
    
    parser = argparse.ArgumentParser(description="Discover infrastructure topology")
    parser.add_argument("--token", required=True, help="DigitalOcean API token")
    parser.add_argument("--user-id", required=True, help="User ID")
    parser.add_argument("--servers", nargs="*", help="Server IPs (optional)")
    parser.add_argument("--project", help="Filter by project")
    parser.add_argument("--env", help="Filter by environment")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    
    args = parser.parse_args()
    
    # Use sync service for CLI
    service = ArchitectureService(args.token, args.user_id)
    topology = service.get_topology(
        server_ips=args.servers,
        project=args.project,
        environment=args.env,
    )
    
    if args.json:
        print(json.dumps(topology.to_dict(), indent=2))
    else:
        print(f"Servers: {len(topology.servers)}")
        print(f"Services: {len(topology.nodes)}")
        print(f"Dependencies: {len(topology.edges)}")
        print(f"Infrastructure: {len(topology.infrastructure)}")
        
        if topology.nodes:
            print("\nServices:")
            for node in topology.nodes:
                print(f"  - {node.id} ({node.type}) on {len(node.servers)} server(s)")
