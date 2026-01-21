"""Networking - Nginx, routing, ports, domains.

Note: SSL certificate management via Let's Encrypt (SSLManager) has been removed.
SSL is now handled via Cloudflare proxy.
"""

from .nginx import (
    NginxConfigGenerator,
    ServerBlock,
    Location,
    Upstream,
    Backend,
    LoadBalanceMethod,
)
from .nginx_manager import (
    NginxManager,
    StreamConfig,
    StreamBackend,
    BackendMode,
    get_internal_port,
    get_host_port,
)
from .ports import DeploymentPortResolver
from .service import NginxService, NginxResult
from .domains import DomainService, DomainResult
from .bulk import BulkNginxService, SyncBulkNginxService, BulkNginxResult

__all__ = [
    # Nginx config generation
    "NginxConfigGenerator",
    "ServerBlock",
    "Location",
    "Upstream",
    "Backend",
    "LoadBalanceMethod",
    # Nginx management
    "NginxManager",
    "StreamConfig",
    "StreamBackend",
    "BackendMode",
    # Nginx service (single server)
    "NginxService",
    "NginxResult",
    # Bulk nginx service (multi-server)
    "BulkNginxService",
    "SyncBulkNginxService",
    "BulkNginxResult",
    # Port resolution
    "DeploymentPortResolver",
    "get_internal_port",
    "get_host_port",
    # Domain provisioning
    "DomainService",
    "DomainResult",
]
