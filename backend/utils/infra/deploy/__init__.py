"""
Deploy module - Service deployment to local and remote targets.

MVP implementation for building and running Docker containers.
"""

from .generator import (
    DockerfileGenerator,
    DockerfileConfig,
    generate_dockerfile,
)

from .local import (
    LocalDeployer,
    RemoteDeployer,
    DeployConfig,
    DeployResult,
)


__all__ = [
    # Generator
    "DockerfileGenerator",
    "DockerfileConfig",
    "generate_dockerfile",
    # Deployers
    "LocalDeployer",
    "RemoteDeployer",
    "DeployConfig",
    "DeployResult",
]
