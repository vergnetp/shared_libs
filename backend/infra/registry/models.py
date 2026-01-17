"""
Service Registry Models.

Data structures for tracking service deployments.
"""

from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime


@dataclass
class ServiceRecord:
    """
    Record of a deployed service instance.
    
    Tracks where a service is running so other services can route to it.
    """
    # Identity
    workspace_id: str           # User/workspace ID
    project: str                # Project name
    environment: str            # prod/staging/dev
    service: str                # Service name (redis, postgres, api, etc.)
    
    # Location
    server_ip: str              # Public IP of server
    host_port: int              # Port mapped on host (for external access)
    container_port: int         # Container's internal port (6379, 5432, etc.)
    container_name: str         # Full container name
    
    # Optional network info
    private_ip: Optional[str] = None  # VPC private IP (preferred for routing)
    internal_port: Optional[int] = None  # Nginx internal port (stable, for service mesh)
    
    # Metadata
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    is_healthy: bool = True
    
    @property
    def is_stateful(self) -> bool:
        """Check if this is a stateful service (needs TCP proxy)."""
        stateful = {"postgres", "postgresql", "mysql", "mariadb", 
                   "redis", "mongo", "mongodb", "opensearch", "elasticsearch"}
        return self.service.lower() in stateful
    
    @property
    def routing_host(self) -> str:
        """Get best host for routing (prefer private IP)."""
        return self.private_ip or self.server_ip
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "workspace_id": self.workspace_id,
            "project": self.project,
            "environment": self.environment,
            "service": self.service,
            "server_ip": self.server_ip,
            "host_port": self.host_port,
            "container_port": self.container_port,
            "container_name": self.container_name,
            "private_ip": self.private_ip,
            "internal_port": self.internal_port,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "is_healthy": self.is_healthy,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "ServiceRecord":
        """Create from dictionary."""
        # Handle datetime parsing
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        elif created_at is None:
            created_at = datetime.utcnow()
            
        updated_at = data.get("updated_at")
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)
        elif updated_at is None:
            updated_at = datetime.utcnow()
        
        return cls(
            workspace_id=data["workspace_id"],
            project=data["project"],
            environment=data["environment"],
            service=data["service"],
            server_ip=data["server_ip"],
            host_port=data["host_port"],
            container_port=data["container_port"],
            container_name=data["container_name"],
            private_ip=data.get("private_ip"),
            internal_port=data.get("internal_port"),
            created_at=created_at,
            updated_at=updated_at,
            is_healthy=data.get("is_healthy", True),
        )


@dataclass
class ProjectServers:
    """
    All servers for a project/environment.
    
    Used to determine which servers need nginx updates.
    """
    workspace_id: str
    project: str
    environment: str
    server_ips: List[str] = field(default_factory=list)
    private_ips: dict = field(default_factory=dict)  # public_ip -> private_ip
    
    def add_server(self, public_ip: str, private_ip: Optional[str] = None):
        """Add a server to the project."""
        if public_ip not in self.server_ips:
            self.server_ips.append(public_ip)
        if private_ip:
            self.private_ips[public_ip] = private_ip
