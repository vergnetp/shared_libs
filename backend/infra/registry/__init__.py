"""
Service Registry - Track where services are deployed.

This module provides a registry for tracking service deployments across servers.
Used by the deployment service to configure nginx stream proxies correctly.

Usage:
    from infra.registry import ServiceRegistry, ServiceRecord
    
    # Register a service
    registry = ServiceRegistry()
    registry.register(ServiceRecord(
        workspace_id="u1",
        project="myapp",
        environment="prod",
        service="redis",
        server_ip="10.0.0.5",
        host_port=8357,
        container_port=6379,
        container_name="u1_myapp_prod_redis",
        private_ip="10.116.0.2",  # VPC
    ))
    
    # Find where a service is
    locations = registry.find_service("u1", "myapp", "prod", "redis")
    
    # Get all servers for a project/env
    servers = registry.get_project_servers("u1", "myapp", "prod")
"""

from .models import ServiceRecord
from .registry import ServiceRegistry, AsyncServiceRegistry

__all__ = [
    "ServiceRecord",
    "ServiceRegistry",
    "AsyncServiceRegistry",
]
