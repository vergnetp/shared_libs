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

from .service import (
    DeploymentService,
    DeploySource,
    MultiDeployConfig,
    MultiDeployResult,
    ServerResult,
    deploy as multi_deploy,
)

from .locks import (
    DeploymentLock,
    DeploymentLockManager,
    get_deployment_lock_manager,
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
    # Multi-server deployment service
    "DeploymentService",
    "DeploySource",
    "MultiDeployConfig",
    "MultiDeployResult", 
    "ServerResult",
    "multi_deploy",
    # Locks
    "DeploymentLock",
    "DeploymentLockManager",
    "get_deployment_lock_manager",
]
