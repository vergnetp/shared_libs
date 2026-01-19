"""
Droplet Sync Service

Syncs DigitalOcean droplets to local database.

Usage:
    from infra.sync import DropletSyncService
    from infra.providers import AsyncDOClient
    
    # In route handler
    do_client = AsyncDOClient(do_token)
    sync_service = DropletSyncService(do_client)
    result = await sync_service.sync(workspace_id, droplet_store)
"""

from __future__ import annotations
import time
from typing import List, Dict, Any, Optional, Set

from .base import SyncResult, DropletStoreProtocol


class DropletSyncService:
    """
    Syncs DO droplets to local database.
    
    Pure logic - no workers, no config. Takes dependencies via injection.
    """
    
    # Tag that identifies droplets managed by this system
    MANAGED_TAG = "deployed-via-api"
    
    def __init__(self, do_client):
        """
        Args:
            do_client: DOClient or AsyncDOClient instance
        """
        self.do_client = do_client
    
    async def sync(
        self,
        workspace_id: str,
        store: DropletStoreProtocol,
        delete_removed: bool = False,
    ) -> SyncResult:
        """
        Sync droplets from DO to local database.
        
        Args:
            workspace_id: User/workspace ID
            store: Droplet store instance (must implement DropletStoreProtocol)
            delete_removed: If True, delete local records for droplets no longer in DO
            
        Returns:
            SyncResult with counts of created/updated/deleted
        """
        start = time.time()
        result = SyncResult()
        
        try:
            # 1. Fetch from DO (only managed droplets)
            do_droplets = await self._fetch_from_do()
            do_by_id: Dict[str, Any] = {str(d.id): d for d in do_droplets}
            
            # 2. Fetch from DB
            db_droplets = await store.list_for_workspace(workspace_id)
            db_by_do_id: Dict[str, Dict] = {
                d.get("do_droplet_id"): d for d in db_droplets 
                if d.get("do_droplet_id")
            }
            
            # 3. Find changes
            do_ids: Set[str] = set(do_by_id.keys())
            db_ids: Set[str] = set(db_by_do_id.keys())
            
            to_create = do_ids - db_ids
            to_update = do_ids & db_ids
            to_delete = db_ids - do_ids if delete_removed else set()
            
            # 4. Apply changes
            for do_id in to_create:
                droplet = do_by_id[do_id]
                await store.upsert_from_do(workspace_id, self._to_dict(droplet))
                result.created += 1
            
            for do_id in to_update:
                droplet = do_by_id[do_id]
                db_record = db_by_do_id[do_id]
                
                # Check if actually changed
                if self._has_changes(droplet, db_record):
                    await store.upsert_from_do(workspace_id, self._to_dict(droplet))
                    result.updated += 1
                else:
                    result.unchanged += 1
            
            for do_id in to_delete:
                await store.delete_by_do_id(workspace_id, do_id)
                result.deleted += 1
                
        except Exception as e:
            result.success = False
            result.errors.append(str(e))
        
        result.duration_ms = (time.time() - start) * 1000
        return result
    
    async def _fetch_from_do(self) -> List[Any]:
        """Fetch managed droplets from DO."""
        # Check if async or sync client
        if hasattr(self.do_client, 'list_droplets'):
            droplets = await self.do_client.list_droplets()
        else:
            droplets = self.do_client.list_droplets()
        
        # Filter to managed only
        return [d for d in droplets if self.MANAGED_TAG in (d.tags or [])]
    
    def _to_dict(self, droplet) -> Dict[str, Any]:
        """Convert DO droplet to dict for storage."""
        # Handle both Droplet dataclass and dict
        if hasattr(droplet, 'to_dict'):
            d = droplet.to_dict()
        elif hasattr(droplet, '__dict__'):
            d = {
                "id": droplet.id,
                "name": droplet.name,
                "ip": getattr(droplet, 'ip', None),
                "private_ip": getattr(droplet, 'private_ip', None),
                "region": droplet.region,
                "size": getattr(droplet, 'size', None),
                "status": droplet.status,
                "tags": droplet.tags,
                "vpc_uuid": getattr(droplet, 'vpc_uuid', None),
                "created_at": getattr(droplet, 'created_at', None),
            }
        else:
            d = dict(droplet)
        
        return {
            "do_droplet_id": str(d.get("id")),
            "name": d.get("name"),
            "public_ip": d.get("ip") or d.get("public_ip"),
            "private_ip": d.get("private_ip"),
            "region": d.get("region"),
            "size": d.get("size"),
            "status": d.get("status", "active"),
            "vpc_uuid": d.get("vpc_uuid"),
            "tags": d.get("tags", []),
        }
    
    def _has_changes(self, do_droplet, db_record: Dict[str, Any]) -> bool:
        """Check if DO droplet differs from DB record."""
        do_dict = self._to_dict(do_droplet)
        
        # Compare key fields
        fields_to_check = ["name", "public_ip", "private_ip", "region", "size", "status", "vpc_uuid"]
        
        for field in fields_to_check:
            if do_dict.get(field) != db_record.get(field):
                return True
        
        return False


class SyncDropletService(DropletSyncService):
    """Sync version of DropletSyncService."""
    
    def sync(
        self,
        workspace_id: str,
        store,
        delete_removed: bool = False,
    ) -> SyncResult:
        """Sync droplets (synchronous version)."""
        import asyncio
        
        # If we're in an async context, just run the parent
        try:
            loop = asyncio.get_running_loop()
            # We're in async context - shouldn't use this class
            raise RuntimeError("Use DropletSyncService for async contexts")
        except RuntimeError:
            # No running loop - create one
            return asyncio.run(super().sync(workspace_id, store, delete_removed))
