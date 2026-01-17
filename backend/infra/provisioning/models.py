"""
Provisioning models.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Generator


@dataclass
class ProvisionRequest:
    """Request to provision a new server."""
    region: str
    size: str = "s-1vcpu-1gb"
    snapshot_id: Optional[str] = None
    name: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    ssh_keys: List[str] = field(default_factory=list)
    vpc_uuid: Optional[str] = None
    project: Optional[str] = None
    environment: str = "prod"


@dataclass
class ProvisionResult:
    """Result of server provisioning."""
    success: bool
    server: Optional[Dict[str, Any]] = None
    vpc_uuid: Optional[str] = None
    error: Optional[str] = None
    
    @property
    def droplet_id(self) -> Optional[str]:
        """Get droplet ID from server dict."""
        if self.server:
            return str(self.server.get("id", ""))
        return None
    
    @property
    def ip(self) -> Optional[str]:
        """Get IP address from server dict."""
        if self.server:
            return self.server.get("ip") or self.server.get("public_ip")
        return None
    
    @property
    def name(self) -> Optional[str]:
        """Get server name from server dict."""
        if self.server:
            return self.server.get("name")
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        result = {"success": self.success}
        if self.server:
            result["server"] = self.server
            result["droplet_id"] = self.droplet_id
            result["ip"] = self.ip
            result["name"] = self.name
        if self.vpc_uuid:
            result["vpc_uuid"] = self.vpc_uuid
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class ProvisionProgress:
    """Progress event during provisioning."""
    type: str  # "progress", "error", "complete"
    message: str
    success: Optional[bool] = None
    ip: Optional[str] = None
    droplet_id: Optional[str] = None
    server_name: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = {"type": self.type, "message": self.message}
        if self.success is not None:
            result["success"] = self.success
        if self.ip:
            result["ip"] = self.ip
        if self.droplet_id:
            result["droplet_id"] = self.droplet_id
        if self.server_name:
            result["server_name"] = self.server_name
        return result
