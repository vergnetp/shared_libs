"""Cloud providers and infrastructure."""

from .digitalocean import DOClient, DOAPIError, Droplet, ServerManager
from .cloudinit import (
    CloudInitConfig,
    build_cloudinit_script,
    SNAPSHOT_PRESETS,
    get_preset,
    get_preset_info,
)
from .snapshot_service import (
    SnapshotService,
    SnapshotConfig,
    SnapshotResult,
    ensure_snapshot,
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
    # DigitalOcean
    "DOClient",
    "DOAPIError",
    "Droplet",
    "ServerManager",
    # Cloud-init
    "CloudInitConfig",
    "build_cloudinit_script",
    "SNAPSHOT_PRESETS",
    "get_preset",
    "get_preset_info",
    # Snapshot service
    "SnapshotService",
    "SnapshotConfig",
    "SnapshotResult",
    "ensure_snapshot",
    # Utilities
    "generate_node_agent_key",
]
