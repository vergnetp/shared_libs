"""Cloud providers and infrastructure.

Provides both sync and async clients:

Sync (for scripts):
    from shared_libs.backend.infra.cloud import DOClient, CloudflareClient, SnapshotService
    
Async (for FastAPI):
    from shared_libs.backend.infra.cloud import AsyncDOClient, AsyncCloudflareClient, AsyncSnapshotService
"""

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
