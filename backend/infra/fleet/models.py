"""
Fleet models - Health and status tracking.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class ServerHealth:
    """Health status of a single server."""
    ip: str
    name: Optional[str] = None
    region: Optional[str] = None
    status: str = "unknown"  # online, unreachable
    agent_version: str = "unknown"
    containers: int = 0
    healthy: int = 0
    unhealthy: int = 0
    health_status: str = "unknown"  # healthy, unhealthy, empty
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "ip": self.ip,
            "status": self.status,
        }
        if self.name:
            result["name"] = self.name
        if self.region:
            result["region"] = self.region
        if self.status == "online":
            result["agent_version"] = self.agent_version
            result["containers"] = self.containers
            result["healthy"] = self.healthy
            result["unhealthy"] = self.unhealthy
            result["health_status"] = self.health_status
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class FleetHealth:
    """Health status of the entire fleet."""
    servers: List[ServerHealth] = field(default_factory=list)
    total: int = 0
    online: int = 0
    healthy: int = 0
    unhealthy: int = 0
    unreachable: int = 0
    status: str = "unknown"  # healthy, degraded, down
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "servers": [s.to_dict() for s in self.servers],
            "summary": {
                "total": self.total,
                "online": self.online,
                "healthy": self.healthy,
                "unhealthy": self.unhealthy,
                "unreachable": self.unreachable,
                "status": self.status,
            }
        }
        if self.error:
            result["error"] = self.error
        return result
