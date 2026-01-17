"""
Architecture topology models.

Dataclasses representing the infrastructure topology:
- Nodes (services, stateful services, proxies)
- Edges (dependencies between services)
- Servers (droplets with container info)
- Infrastructure (nginx, agents, etc.)
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class ServerInfo:
    """Information about a server in the topology."""
    ip: str
    container_port: Optional[int] = None
    nginx_status: str = "unknown"


@dataclass
class ServiceNode:
    """A service node in the architecture graph."""
    id: str
    container_name: str
    type: str  # "service", "stateful", "proxy"
    service: str
    project: str
    env: str
    status: str = "running"
    ports: List[str] = field(default_factory=list)
    container_port: Optional[int] = None
    host_port: Optional[int] = None
    internal_port: Optional[int] = None
    domain: Optional[str] = None
    servers: List[ServerInfo] = field(default_factory=list)
    image: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "container_name": self.container_name,
            "type": self.type,
            "service": self.service,
            "project": self.project,
            "env": self.env,
            "status": self.status,
            "ports": self.ports,
            "container_port": self.container_port,
            "host_port": self.host_port,
            "internal_port": self.internal_port,
            "domain": self.domain,
            "servers": [
                {"ip": s.ip, "container_port": s.container_port, "nginx_status": s.nginx_status}
                if isinstance(s, ServerInfo) else s
                for s in self.servers
            ],
            "image": self.image,
        }


@dataclass
class ServiceEdge:
    """An edge (dependency) between services."""
    from_node: str
    to_node: str
    type: str = "depends_on"
    label: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "from": self.from_node,
            "to": self.to_node,
            "type": self.type,
            "label": self.label,
        }


@dataclass
class InfrastructureComponent:
    """Infrastructure component (nginx, agent, etc.)."""
    name: str
    type: str  # "nginx", "agent", "proxy"
    server_ip: str
    status: str = "running"
    ports: List[str] = field(default_factory=list)
    image: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "server_ip": self.server_ip,
            "status": self.status,
            "ports": self.ports,
            "image": self.image,
        }


@dataclass
class ServerStatus:
    """Status information for a server."""
    ip: str
    containers: int = 0
    status: str = "unknown"  # "online", "error"
    error: Optional[str] = None
    nginx_status: str = "unknown"
    agent_version: str = "unknown"
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "ip": self.ip,
            "containers": self.containers,
            "status": self.status,
            "nginx_status": self.nginx_status,
            "agent_version": self.agent_version,
        }
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class ArchitectureTopology:
    """Complete architecture topology result."""
    nodes: List[ServiceNode] = field(default_factory=list)
    edges: List[ServiceEdge] = field(default_factory=list)
    servers: List[ServerStatus] = field(default_factory=list)
    infrastructure: List[InfrastructureComponent] = field(default_factory=list)
    filters: Dict[str, Optional[str]] = field(default_factory=dict)
    message: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "servers": [s.to_dict() for s in self.servers],
            "infrastructure": [i.to_dict() for i in self.infrastructure],
            "filters": self.filters,
        }
        if self.message:
            result["message"] = self.message
        return result
