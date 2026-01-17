"""
Orchestration models - Deployment events and results.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum
import json


class EventType(str, Enum):
    """Types of deployment events."""
    LOG = "log"
    PROGRESS = "progress"
    SERVER_READY = "server_ready"
    DEPLOY_START = "deploy_start"
    DEPLOY_SUCCESS = "deploy_success"
    DEPLOY_FAILURE = "deploy_failure"
    DONE = "done"
    PING = "ping"
    ERROR = "error"


@dataclass
class DeployEvent:
    """A deployment event for streaming."""
    type: EventType
    message: Optional[str] = None
    progress: Optional[int] = None
    server_ip: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    success: Optional[bool] = None
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = {"type": self.type.value if isinstance(self.type, EventType) else self.type}
        if self.message:
            result["message"] = self.message
        if self.progress is not None:
            result["progress"] = self.progress
        if self.server_ip:
            result["server_ip"] = self.server_ip
        if self.data:
            result.update(self.data)
        if self.success is not None:
            result["success"] = self.success
        if self.error:
            result["error"] = self.error
        return result
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict())
    
    def to_sse(self) -> str:
        """Format as Server-Sent Event."""
        return f"data: {self.to_json()}\n\n"


@dataclass
class DeployConfig:
    """Configuration for a deployment."""
    image: str
    name: str  # Service name
    port: int = 8000
    container_port: Optional[int] = None
    host_port: Optional[int] = None
    project: Optional[str] = None
    environment: str = "prod"
    env_vars: Dict[str, str] = field(default_factory=dict)
    volumes: List[str] = field(default_factory=list)
    server_ips: List[str] = field(default_factory=list)
    new_server_count: int = 0
    snapshot_id: Optional[str] = None
    region: str = "lon1"
    size: str = "s-1vcpu-1gb"
    auto_env: bool = False
    depends_on: List[str] = field(default_factory=list)
    persist_data: bool = False
    
    @property
    def effective_port(self) -> int:
        return self.host_port or self.port
    
    @property
    def effective_container_port(self) -> int:
        return self.container_port or self.port


@dataclass
class DeployResult:
    """Result of a deployment."""
    success: bool
    container_name: Optional[str] = None
    servers: List[Dict[str, Any]] = field(default_factory=list)
    urls: List[str] = field(default_factory=list)
    domain: Optional[str] = None
    deployment_id: Optional[str] = None
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = {"success": self.success}
        if self.container_name:
            result["container_name"] = self.container_name
        if self.servers:
            result["servers"] = self.servers
        if self.urls:
            result["urls"] = self.urls
        if self.domain:
            result["domain"] = self.domain
        if self.deployment_id:
            result["deployment_id"] = self.deployment_id
        if self.error:
            result["error"] = self.error
        return result


@dataclass 
class RollbackConfig:
    """Configuration for a rollback."""
    deployment_id: str
    server_ips: List[str] = field(default_factory=list)


@dataclass
class RollbackResult:
    """Result of a rollback."""
    success: bool
    rolled_back_to: Optional[str] = None
    servers: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = {"success": self.success}
        if self.rolled_back_to:
            result["rolled_back_to"] = self.rolled_back_to
        if self.servers:
            result["servers"] = self.servers
        if self.error:
            result["error"] = self.error
        return result
