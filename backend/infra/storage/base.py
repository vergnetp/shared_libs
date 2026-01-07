"""
Storage Backend - Abstract base class for deployment storage.

Provides pluggable storage for:
- Project configurations
- Deployment state
- Credentials (encrypted)
- Server inventory
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum


class StorageError(Exception):
    """Base exception for storage operations."""
    pass


class StorageNotFoundError(StorageError):
    """Raised when requested item doesn't exist."""
    pass


class StorageBackend(ABC):
    """
    Abstract storage backend for deployment data.
    
    Implementations:
    - FileStorageBackend: JSON files (standalone/CLI usage)
    - DatabaseStorageBackend: Database entities (deploy_api)
    
    All methods are async for consistency, even if underlying
    implementation is synchronous.
    """
    
    # =========================================================================
    # Project Configuration
    # =========================================================================
    
    @abstractmethod
    async def get_project(
        self, 
        user_id: str, 
        project_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get project configuration.
        
        Args:
            user_id: Tenant/workspace ID
            project_name: Project name
            
        Returns:
            Project config dict or None if not found
        """
        pass
    
    @abstractmethod
    async def save_project(
        self, 
        user_id: str, 
        project_name: str, 
        config: Dict[str, Any]
    ) -> None:
        """
        Save project configuration.
        
        Args:
            user_id: Tenant/workspace ID
            project_name: Project name
            config: Full project configuration
        """
        pass
    
    @abstractmethod
    async def delete_project(
        self, 
        user_id: str, 
        project_name: str
    ) -> bool:
        """
        Delete project configuration.
        
        Returns:
            True if deleted, False if didn't exist
        """
        pass
    
    @abstractmethod
    async def list_projects(self, user_id: str) -> List[str]:
        """
        List all projects for a user.
        
        Returns:
            List of project names
        """
        pass
    
    # =========================================================================
    # Deployment State
    # =========================================================================
    
    @abstractmethod
    async def get_deployment_state(
        self, 
        user_id: str, 
        project_name: str, 
        env: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get current deployment state.
        
        State includes:
        - Running containers
        - Current ports (primary/secondary)
        - Last deployment timestamp
        - Service health status
        
        Returns:
            State dict or None if no deployment
        """
        pass
    
    @abstractmethod
    async def save_deployment_state(
        self, 
        user_id: str, 
        project_name: str, 
        env: str, 
        state: Dict[str, Any]
    ) -> None:
        """
        Save deployment state.
        
        Called after each deployment to track current state.
        """
        pass
    
    # =========================================================================
    # Credentials (Encrypted)
    # =========================================================================
    
    @abstractmethod
    async def get_credentials(
        self, 
        user_id: str, 
        project_name: str, 
        env: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get deployment credentials.
        
        Credentials include:
        - Database passwords
        - API keys
        - SSH keys
        - Docker registry tokens
        
        Note: Credentials are stored encrypted. Backend handles
        encryption/decryption transparently.
        
        Returns:
            Decrypted credentials dict or None
        """
        pass
    
    @abstractmethod
    async def save_credentials(
        self, 
        user_id: str, 
        project_name: str, 
        env: str, 
        credentials: Dict[str, Any]
    ) -> None:
        """
        Save deployment credentials (will be encrypted).
        """
        pass
    
    # =========================================================================
    # Server Inventory
    # =========================================================================
    
    @abstractmethod
    async def get_servers(
        self, 
        user_id: str, 
        project_name: Optional[str] = None,
        env: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get server inventory.
        
        Args:
            user_id: Tenant ID
            project_name: Filter by project (optional)
            env: Filter by environment (optional)
            zone: Filter by zone/region (optional)
            
        Returns:
            List of server dicts with IP, zone, status, etc.
        """
        pass
    
    @abstractmethod
    async def save_server(
        self, 
        user_id: str, 
        server: Dict[str, Any]
    ) -> None:
        """
        Save/update server in inventory.
        
        Server dict should include:
        - id: Unique identifier
        - ip: IP address
        - zone: Region/zone
        - status: active/inactive/provisioning
        - droplet_id: Cloud provider ID (if applicable)
        """
        pass
    
    @abstractmethod
    async def delete_server(
        self, 
        user_id: str, 
        server_id: str
    ) -> bool:
        """
        Remove server from inventory.
        
        Returns:
            True if deleted, False if didn't exist
        """
        pass
    
    # =========================================================================
    # Deployment History (Optional)
    # =========================================================================
    
    async def get_deployment_history(
        self,
        user_id: str,
        project_name: str,
        env: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Get deployment history.
        
        Optional - not all backends need to implement.
        Default returns empty list.
        """
        return []
    
    async def save_deployment_record(
        self,
        user_id: str,
        project_name: str,
        env: str,
        record: Dict[str, Any],
    ) -> None:
        """
        Save deployment record to history.
        
        Optional - default is no-op.
        """
        pass
    
    # =========================================================================
    # Generic Key-Value (for extensions)
    # =========================================================================
    
    async def get(
        self, 
        user_id: str, 
        key: str
    ) -> Optional[Any]:
        """
        Generic key-value get.
        
        For storing arbitrary data not covered by specific methods.
        Default implementation returns None.
        """
        return None
    
    async def set(
        self, 
        user_id: str, 
        key: str, 
        value: Any
    ) -> None:
        """
        Generic key-value set.
        
        Default implementation is no-op.
        """
        pass
    
    async def delete(
        self, 
        user_id: str, 
        key: str
    ) -> bool:
        """
        Generic key-value delete.
        
        Default returns False.
        """
        return False


@dataclass
class ServerInfo:
    """Server information structure."""
    id: str
    ip: str
    zone: str
    status: str = "active"
    droplet_id: Optional[str] = None
    hostname: Optional[str] = None
    project_name: Optional[str] = None
    env: Optional[str] = None
    tags: List[str] = None
    created_at: Optional[str] = None
    
    def __post_init__(self):
        if self.tags is None:
            self.tags = []
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "ip": self.ip,
            "zone": self.zone,
            "status": self.status,
            "droplet_id": self.droplet_id,
            "hostname": self.hostname,
            "project_name": self.project_name,
            "env": self.env,
            "tags": self.tags,
            "created_at": self.created_at,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ServerInfo':
        return cls(
            id=data["id"],
            ip=data["ip"],
            zone=data["zone"],
            status=data.get("status", "active"),
            droplet_id=data.get("droplet_id"),
            hostname=data.get("hostname"),
            project_name=data.get("project_name"),
            env=data.get("env"),
            tags=data.get("tags", []),
            created_at=data.get("created_at"),
        )
