"""
Architecture module - Infrastructure topology discovery.

Provides tools to discover and visualize deployed services across servers.

Usage (Sync - for CLI/scripts):
    from infra.architecture import ArchitectureService
    
    service = ArchitectureService(do_token, user_id)
    topology = service.get_topology()

Usage (Async - for FastAPI):
    from infra.architecture import AsyncArchitectureService
    
    service = AsyncArchitectureService(do_token, user_id)
    topology = await service.get_topology()
"""

from .service import ArchitectureService, AsyncArchitectureService
from .models import (
    ArchitectureTopology,
    ServiceNode,
    ServiceEdge,
    ServerStatus,
    InfrastructureComponent,
    ServerInfo,
)

__all__ = [
    # Services
    "ArchitectureService",       # Sync (CLI/scripts)
    "AsyncArchitectureService",  # Async (FastAPI)
    # Models
    "ArchitectureTopology",
    "ServiceNode",
    "ServiceEdge",
    "ServerStatus",
    "InfrastructureComponent",
    "ServerInfo",
]
