"""DigitalOcean cloud provider.

Provides both sync and async clients:
- DOClient (sync) - for scripts
- AsyncDOClient (async) - for FastAPI

Usage:
    # Sync
    from shared_libs.backend.infra.providers.digitalocean import DOClient
    client = DOClient(token)
    droplet = client.create_droplet(...)
    
    # Async
    from shared_libs.backend.infra.providers.digitalocean import AsyncDOClient
    client = AsyncDOClient(token)
    droplet = await client.create_droplet(...)
"""

from .client import (
    DOClient,
    AsyncDOClient,
    DOAPIError,
    ServerManager,
    MANAGED_TAG,
    Droplet,
    Result,
    DOError,
)

# Re-export shared cloud types (relative import)
from ....cloud.digitalocean import DropletSize, Region

__all__ = [
    # Sync client
    "DOClient",
    # Async client
    "AsyncDOClient",
    # Types
    "DOAPIError", 
    "DOError",
    "Droplet",
    "DropletSize",
    "Region",
    "Result",
    # Helpers
    "ServerManager",
    "MANAGED_TAG",
]
