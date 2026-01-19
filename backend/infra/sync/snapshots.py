"""
Snapshot Sync Service

Syncs DigitalOcean snapshots to local database.

Usage:
    from infra.sync import SnapshotSyncService
    from infra.providers import AsyncDOClient
    
    # In route handler
    do_client = AsyncDOClient(do_token)
    sync_service = SnapshotSyncService(do_client)
    result = await sync_service.sync(workspace_id, snapshot_store)
"""

from __future__ import annotations
import time
from typing import List, Dict, Any, Set

from .base import SyncResult, SnapshotStoreProtocol


class SnapshotSyncService:
    """
    Syncs DO snapshots to local database.
    
    Pure logic - no workers, no config. Takes dependencies via injection.
    """
    
    def __init__(self, do_client):
        """
        Args:
            do_client: DOClient or AsyncDOClient instance
        """
        self.do_client = do_client
    
    async def sync(
        self,
        workspace_id: str,
        store: SnapshotStoreProtocol,
        delete_removed: bool = True,  # Snapshots more likely to be deleted
    ) -> SyncResult:
        """
        Sync snapshots from DO to local database.
        
        Args:
            workspace_id: User/workspace ID
            store: Snapshot store instance (must implement SnapshotStoreProtocol)
            delete_removed: If True, delete local records for snapshots no longer in DO
            
        Returns:
            SyncResult with counts of created/updated/deleted
        """
        start = time.time()
        result = SyncResult()
        
        try:
            # 1. Fetch from DO
            do_snapshots = await self._fetch_from_do()
            do_by_id: Dict[str, Dict] = {str(s["id"]): s for s in do_snapshots}
            
            # 2. Fetch from DB
            db_snapshots = await store.list_for_workspace(workspace_id)
            db_by_do_id: Dict[str, Dict] = {
                s.get("do_snapshot_id"): s for s in db_snapshots
                if s.get("do_snapshot_id")
            }
            
            # 3. Find changes
            do_ids: Set[str] = set(do_by_id.keys())
            db_ids: Set[str] = set(db_by_do_id.keys())
            
            to_create = do_ids - db_ids
            to_update = do_ids & db_ids
            to_delete = db_ids - do_ids if delete_removed else set()
            
            # 4. Apply changes
            for do_id in to_create:
                snapshot = do_by_id[do_id]
                await store.upsert_from_do(workspace_id, snapshot)
                result.created += 1
            
            for do_id in to_update:
                snapshot = do_by_id[do_id]
                db_record = db_by_do_id[do_id]
                
                if self._has_changes(snapshot, db_record):
                    await store.upsert_from_do(workspace_id, snapshot)
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
    
    async def _fetch_from_do(self) -> List[Dict[str, Any]]:
        """Fetch snapshots from DO."""
        import asyncio
        
        # The SnapshotService returns dicts, not dataclasses
        # Check if we have async method
        if hasattr(self.do_client, 'list_snapshots_async'):
            snapshots = await self.do_client.list_snapshots_async()
        elif hasattr(self.do_client, 'list_snapshots'):
            # Sync method - run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            snapshots = await loop.run_in_executor(None, self.do_client.list_snapshots)
            snapshots = snapshots if isinstance(snapshots, list) else []
        else:
            snapshots = []
        
        return snapshots
    
    def _has_changes(self, do_snapshot: Dict[str, Any], db_record: Dict[str, Any]) -> bool:
        """Check if DO snapshot differs from DB record."""
        fields_to_check = ["name", "size_gigabytes", "regions"]
        
        for field in fields_to_check:
            do_val = do_snapshot.get(field)
            db_val = db_record.get(field)
            if do_val != db_val:
                return True
        
        return False


class SyncSnapshotService(SnapshotSyncService):
    """Sync version of SnapshotSyncService."""
    
    def sync(
        self,
        workspace_id: str,
        store,
        delete_removed: bool = True,
    ) -> SyncResult:
        """Sync snapshots (synchronous version)."""
        import asyncio
        
        try:
            loop = asyncio.get_running_loop()
            raise RuntimeError("Use SnapshotSyncService for async contexts")
        except RuntimeError:
            return asyncio.run(super().sync(workspace_id, store, delete_removed))
