"""Cloud providers and infrastructure.

Provides both sync and async clients:

Sync (for scripts):
    from shared_libs.backend.infra.cloud import DOClient, CloudflareClient, SnapshotService
    
Async (for FastAPI):
    from shared_libs.backend.infra.cloud import AsyncDOClient, AsyncCloudflareClient, AsyncSnapshotService

Connection Pooling (RECOMMENDED for FastAPI - uses http_client pool):
    from shared_libs.backend.infra.cloud import get_do_client, get_cf_client
    
    # Reuses connections - much faster!
    client = await get_do_client(token)
    droplets = await client.list_droplets()
    # Don't close - pool manages lifecycle

Response Caching (from http_client):
    from shared_libs.backend.http_client import cached_request
    
    @cached_request(ttl=30)
    async def list_droplets(token):
        client = await get_do_client(token)
        return await client.list_droplets()
"""

# DigitalOcean regions
DO_REGIONS = [
    {"slug": "nyc1", "name": "New York 1", "available": True},
    {"slug": "nyc3", "name": "New York 3", "available": True},
    {"slug": "ams3", "name": "Amsterdam 3", "available": True},
    {"slug": "sfo2", "name": "San Francisco 2", "available": True},
    {"slug": "sfo3", "name": "San Francisco 3", "available": True},
    {"slug": "sgp1", "name": "Singapore 1", "available": True},
    {"slug": "lon1", "name": "London 1", "available": True},
    {"slug": "fra1", "name": "Frankfurt 1", "available": True},
    {"slug": "tor1", "name": "Toronto 1", "available": True},
    {"slug": "blr1", "name": "Bangalore 1", "available": True},
    {"slug": "syd1", "name": "Sydney 1", "available": True},
]

# Common droplet sizes
DROPLET_SIZES = [
    {"slug": "s-1vcpu-512mb-10gb", "memory": 512, "vcpus": 1, "disk": 10, "price_monthly": 4},
    {"slug": "s-1vcpu-1gb", "memory": 1024, "vcpus": 1, "disk": 25, "price_monthly": 6},
    {"slug": "s-1vcpu-2gb", "memory": 2048, "vcpus": 1, "disk": 50, "price_monthly": 12},
    {"slug": "s-2vcpu-2gb", "memory": 2048, "vcpus": 2, "disk": 60, "price_monthly": 18},
    {"slug": "s-2vcpu-4gb", "memory": 4096, "vcpus": 2, "disk": 80, "price_monthly": 24},
    {"slug": "s-4vcpu-8gb", "memory": 8192, "vcpus": 4, "disk": 160, "price_monthly": 48},
    {"slug": "s-8vcpu-16gb", "memory": 16384, "vcpus": 8, "disk": 320, "price_monthly": 96},
]

# DigitalOcean - sync and async
from .digitalocean import (
    DOClient,
    AsyncDOClient,
    DOAPIError,
    DOError,
    Droplet,
    DropletSize,
    Region,
    Result,
    ServerManager,
    MANAGED_TAG,
)

# Cloudflare - sync and async
from .cloudflare import (
    CloudflareClient,
    AsyncCloudflareClient,
    CloudflareError,
    DNSRecord,
)

# Cloud-init
from .cloudinit import (
    CloudInitConfig,
    build_cloudinit_script,
    SNAPSHOT_PRESETS,
    get_preset,
    get_preset_info,
)

# Snapshot service - sync and async
from .snapshot_service import (
    SnapshotService,
    AsyncSnapshotService,
    SnapshotConfig,
    SnapshotResult,
    ensure_snapshot,
    ensure_snapshot_async,
)


# Note: Connection pooling is now automatic in AsyncBaseCloudClient.
# Just use AsyncDOClient(token) directly - connections are reused.
# The get_do_client/get_cf_client functions below are kept for convenience
# but are now optional since pooling happens at the HTTP level.

async def get_do_client(token: str) -> AsyncDOClient:
    """
    Get a DigitalOcean client.
    
    Note: Connection pooling is automatic - you can also just use AsyncDOClient(token).
    """
    return AsyncDOClient(token)


async def get_cf_client(token: str) -> AsyncCloudflareClient:
    """
    Get a Cloudflare client.
    
    Note: Connection pooling is automatic - you can also just use AsyncCloudflareClient(token).
    """
    return AsyncCloudflareClient(token)


async def close_all_clients():
    """No-op: connection pool is managed by base class."""
    pass
    _do_clients.clear()
    _cf_clients.clear()


def generate_node_agent_key(do_token: str, user_id: str = "") -> str:
    """
    Generate the node agent API key from DO token.
    
    This is deterministic - same inputs always produce same key.
    User can call this anytime to get their key without storing it.
    
    Args:
        do_token: DigitalOcean API token
        user_id: Optional user ID for multi-tenant scenarios
        
    Returns:
        32-character API key
        
    Example:
        key = generate_node_agent_key("dop_v1_abc123...")
        # Use this key for X-API-Key header
    """
    return SnapshotService.generate_api_key(do_token, user_id)


__all__ = [
    # Constants
    "DO_REGIONS",
    "DROPLET_SIZES",
    # DigitalOcean - sync
    "DOClient",
    "DOAPIError",
    "DOError",
    "Droplet",
    "DropletSize",
    "Region",
    "Result",
    "ServerManager",
    "MANAGED_TAG",
    # DigitalOcean - async
    "AsyncDOClient",
    # Cloudflare - sync
    "CloudflareClient",
    "CloudflareError",
    "DNSRecord",
    # Cloudflare - async
    "AsyncCloudflareClient",
    # Cloud-init
    "CloudInitConfig",
    "build_cloudinit_script",
    "SNAPSHOT_PRESETS",
    "get_preset",
    "get_preset_info",
    # Snapshot service - sync
    "SnapshotService",
    "SnapshotConfig",
    "SnapshotResult",
    "ensure_snapshot",
    # Snapshot service - async
    "AsyncSnapshotService",
    "ensure_snapshot_async",
    # Utilities
    "generate_node_agent_key",
]
