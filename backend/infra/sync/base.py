"""
Sync Module Base Classes

Provides base classes for syncing external API data to local database.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional, Protocol, runtime_checkable


@dataclass
class SyncResult:
    """Result of a sync operation."""
    
    success: bool = True
    created: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0
    errors: List[str] = field(default_factory=list)
    duration_ms: Optional[float] = None
    synced_at: Optional[str] = None
    
    def __post_init__(self):
        if self.synced_at is None:
            self.synced_at = datetime.utcnow().isoformat()
    
    @property
    def total_changes(self) -> int:
        return self.created + self.updated + self.deleted
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "created": self.created,
            "updated": self.updated,
            "deleted": self.deleted,
            "unchanged": self.unchanged,
            "total_changes": self.total_changes,
            "errors": self.errors,
            "duration_ms": self.duration_ms,
            "synced_at": self.synced_at,
        }


@runtime_checkable
class DropletStoreProtocol(Protocol):
    """Protocol for droplet store - allows dependency injection."""
    
    async def list_for_workspace(self, workspace_id: str) -> List[Dict[str, Any]]:
        """List all droplets for a workspace."""
        ...
    
    async def upsert_from_do(self, workspace_id: str, do_droplet: Dict[str, Any]) -> Dict[str, Any]:
        """Upsert a droplet from DO API data."""
        ...
    
    async def delete_by_do_id(self, workspace_id: str, do_droplet_id: str) -> bool:
        """Delete a droplet by its DO ID."""
        ...
    
    async def get_by_do_id(self, workspace_id: str, do_droplet_id: str) -> Optional[Dict[str, Any]]:
        """Get a droplet by its DO ID."""
        ...


@runtime_checkable
class SnapshotStoreProtocol(Protocol):
    """Protocol for snapshot store - allows dependency injection."""
    
    async def list_for_workspace(self, workspace_id: str) -> List[Dict[str, Any]]:
        """List all snapshots for a workspace."""
        ...
    
    async def upsert_from_do(self, workspace_id: str, do_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Upsert a snapshot from DO API data."""
        ...
    
    async def delete_by_do_id(self, workspace_id: str, do_snapshot_id: str) -> bool:
        """Delete a snapshot by its DO ID."""
        ...
