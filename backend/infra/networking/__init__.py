"""Networking - Nginx, SSL, routing, ports, domains."""

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
from .ssl import (
    SSLManager,
    Certificate,
)
from .service import NginxService, NginxResult
from .domains import DomainService, DomainResult

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
    # Nginx service (high-level)
    "NginxService",
    "NginxResult",
    # Port resolution
    "DeploymentPortResolver",
    "get_internal_port",
    "get_host_port",
    # SSL
    "SSLManager",
    "Certificate",
    # Domain provisioning
    "DomainService",
    "DomainResult",
]
