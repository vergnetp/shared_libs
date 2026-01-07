"""
Infra - Multi-tenant Docker Deployment System

A clean, context-driven deployment system for Docker containers.

Quick Start:
    from backend.infra import DeploymentContext, Deployer, Service
    
    # Create context
    ctx = DeploymentContext(
        user_id="workspace_123",
        project_name="myapp",
        env="prod",
        storage=storage_backend,  # Optional
    )
    
    # Deploy
    deployer = Deployer(ctx)
    result = deployer.deploy()

Define Services:
    from backend.infra import Service
    
    # Predefined service types
    postgres = Service.postgres()
    redis = Service.redis()
    
    # Custom service
    api = Service(
        name="api",
        image="myapp/api:latest",
        ports=[ServicePort(container_port=8000)],
        environment={"DEBUG": "false"},
        depends_on=["postgres", "redis"],
    )

Storage Backends:
    # File-based (standalone/CLI)
    from backend.infra import FileStorageBackend
    storage = FileStorageBackend(base_path="/config")
    
    # Database (deploy_api)
    from backend.infra import DatabaseStorageBackend
    storage = DatabaseStorageBackend(get_db_connection)
"""

__version__ = "2.0.0"

# Context
from .context import DeploymentContext, Context

# Storage backends
from .storage import (
    StorageBackend,
    StorageError,
    StorageNotFoundError,
    FileStorageBackend,
    DatabaseStorageBackend,
)

# Core
from .core import (
    Deployer,
    Service,
    ServiceType,
    ServicePort,
    ServiceVolume,
    ServiceHealthCheck,
    RestartPolicy,
    Result,
    DeployResult,
    ContainerResult,
    BuildResult,
    Status,
)

# Docker
from .docker import DockerClient, Container

# SSH
from .ssh import SSHClient, SSHConfig

__all__ = [
    # Version
    "__version__",
    
    # Context
    "DeploymentContext",
    "Context",
    
    # Storage
    "StorageBackend",
    "StorageError",
    "StorageNotFoundError",
    "FileStorageBackend",
    "DatabaseStorageBackend",
    
    # Core
    "Deployer",
    "Service",
    "ServiceType",
    "ServicePort",
    "ServiceVolume",
    "ServiceHealthCheck",
    "RestartPolicy",
    "Result",
    "DeployResult",
    "ContainerResult",
    "BuildResult",
    "Status",
    
    # Docker
    "DockerClient",
    "Container",
    
    # SSH
    "SSHClient",
    "SSHConfig",
]
