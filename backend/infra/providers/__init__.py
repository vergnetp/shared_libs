"""Cloud providers - Extended clients with infra-specific functionality.

This module provides extended cloud clients that inherit from the base
cloud/ module and add infrastructure-specific methods.

Provides both sync and async clients:

Sync (for scripts):
    from shared_libs.backend.infra.providers import DOClient, CloudflareClient, SnapshotService
    
Async (for FastAPI):
    from shared_libs.backend.infra.providers import AsyncDOClient, AsyncCloudflareClient, AsyncSnapshotService

Connection Pooling (automatic via base cloud/ module):
    client = AsyncDOClient(token)
    droplets = await client.list_droplets()
    # Connections are automatically pooled and reused
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

# ============================================================================
# DigitalOcean clients - EXTENDED from infra/providers/digitalocean/
# These extend the base cloud/ clients with infra-specific methods
# ============================================================================
from .digitalocean import (
    DOClient,           # Extended with ensure_docker_snapshot, etc.
    AsyncDOClient,      # Extended with async infra methods
    DOAPIError,
    DOError,
    Droplet,
    Result,
    MANAGED_TAG,
    ServerManager,      # Infra-specific server management
)

# Legacy aliases from shared cloud module
try:
    from ...cloud.digitalocean import DropletSize, Region
except ImportError:
    # Define locally if not in new module
    from dataclasses import dataclass
    
    @dataclass
    class DropletSize:
        slug: str
        memory: int
        vcpus: int
        disk: int
        price_monthly: float
    
    @dataclass
    class Region:
        slug: str
        name: str
        available: bool

# ============================================================================
# Cloudflare clients - EXTENDED from infra/providers/cloudflare.py
# These extend the base cloud/ clients with infra-specific methods
# ============================================================================
from .cloudflare import (
    CloudflareClient,       # Extended with delete_all_a_records, etc.
    AsyncCloudflareClient,  # Extended with async infra methods
    CloudflareError,
    DNSRecord,
)

# ============================================================================
# Infra-specific: Cloud-init
# ============================================================================
from .cloudinit import (
    CloudInitConfig,
    build_cloudinit_script,
    SNAPSHOT_PRESETS,
    get_preset,
    get_preset_info,
)

# ============================================================================
# Infra-specific: Snapshot service - sync and async
# ============================================================================
from .snapshot_service import (
    SnapshotService,
    AsyncSnapshotService,
    SnapshotConfig,
    SnapshotResult,
    ensure_snapshot,
    ensure_snapshot_async,
)


# ============================================================================
# VPC Detection - for optimal routing (private IP when in VPC)
# ============================================================================
from .vpc_detection import (
    is_in_vpc,
    get_vpc_ip,
    get_local_ips,
    get_best_ip_for_target,
    get_current_droplet_id,
    get_do_metadata,
    get_routing_debug_info,
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


# Convenience functions (optional - clients work directly now)
async def get_do_client(token: str) -> AsyncDOClient:
    """Get a DigitalOcean client. Pooling is automatic."""
    return AsyncDOClient(token)


async def get_cf_client(token: str) -> AsyncCloudflareClient:
    """Get a Cloudflare client. Pooling is automatic."""
    return AsyncCloudflareClient(token)


async def close_all_clients():
    """No-op: connection pool is managed by http_client module."""
    pass


__all__ = [
    # Constants
    "DO_REGIONS",
    "DROPLET_SIZES",
    # DigitalOcean - extended from cloud/ module
    "DOClient",
    "AsyncDOClient",
    "DOAPIError",
    "DOError",
    "Droplet",
    "DropletSize",
    "Region",
    "Result",
    "MANAGED_TAG",
    "ServerManager",
    # Cloudflare - extended from cloud/ module
    "CloudflareClient",
    "AsyncCloudflareClient",
    "CloudflareError",
    "DNSRecord",
    # Cloud-init - infra specific
    "CloudInitConfig",
    "build_cloudinit_script",
    "SNAPSHOT_PRESETS",
    "get_preset",
    "get_preset_info",
    # Snapshot service - infra specific
    "SnapshotService",
    "AsyncSnapshotService",
    "SnapshotConfig",
    "SnapshotResult",
    "ensure_snapshot",
    "ensure_snapshot_async",
    # VPC detection - for optimal routing
    "is_in_vpc",
    "get_vpc_ip",
    "get_local_ips",
    "get_best_ip_for_target",
    "get_current_droplet_id",
    "get_do_metadata",
    "get_routing_debug_info",
    # Utilities - infra specific
    "generate_node_agent_key",
    # Convenience functions
    "get_do_client",
    "get_cf_client",
    "close_all_clients",
]
