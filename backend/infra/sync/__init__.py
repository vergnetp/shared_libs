"""
Sync Module - Synchronize external API data to local database.

This module provides services for syncing data from external APIs (like DigitalOcean)
to local database storage. This enables:
- Fast local queries (no external API latency)
- Reduced API rate limit usage
- Offline access to cached data
- Background refresh without blocking user requests

Usage:
    from infra.sync import DropletSyncService, SnapshotSyncService, SyncResult
    from infra.providers import AsyncDOClient
    
    # Create sync service
    do_client = AsyncDOClient(do_token)
    sync_service = DropletSyncService(do_client)
    
    # Sync to database
    result = await sync_service.sync(workspace_id, droplet_store)
    
    print(f"Created: {result.created}, Updated: {result.updated}")

Architecture:
    - Pure logic - no workers, no config in this module
    - Takes stores via dependency injection (duck-typed via Protocol)
    - Returns SyncResult with change counts
    - Caller decides when to sync (on request, on timer, etc.)
"""

from .base import SyncResult, DropletStoreProtocol, SnapshotStoreProtocol
from .droplets import DropletSyncService, SyncDropletService
from .snapshots import SnapshotSyncService, SyncSnapshotService

__all__ = [
    # Results
    "SyncResult",
    
    # Protocols (for type hints)
    "DropletStoreProtocol",
    "SnapshotStoreProtocol",
    
    # Async services
    "DropletSyncService",
    "SnapshotSyncService",
    
    # Sync services
    "SyncDropletService",
    "SyncSnapshotService",
]
