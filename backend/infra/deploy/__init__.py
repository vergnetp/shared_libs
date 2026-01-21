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

from .env_builder import (
    DeployEnvBuilder,
    build_deploy_env,
    build_deploy_volumes,
    build_stateful_service_env,
    build_discovered_service_urls,
    is_stateful_service,
    get_service_container_port,
    get_connection_info,
    KNOWN_SERVICES,
    DEFAULT_HTTP_PORT,
    # Stateful service types (for UI)
    STATEFUL_SERVICE_TYPES,
    get_stateful_service_types,
    get_stateful_image,
)

from .injection import (
    DiscoveredService,
    ServiceNeedingRedeploy,
    ServiceDiscovery,
    InjectionContext,
    build_injection_env_vars,
    get_env_var_name_for_service,
    find_services_needing_redeploy,
    format_redeploy_warning,
)

from .history import (
    DeploymentHistory,
    DeploymentRecord,
    DeploymentStatus,
    get_deployment_history,
)

from .rollback import (
    RollbackHelper,
    RollbackMetadata,
)

from .orchestrator import (
    DeployJobConfig,
    deploy_task,
    rollback_task,
    stateful_deploy_task,
    DEPLOY_TASKS,
)


__all__ = [
    # Generator
    "DockerfileGenerator",
    "DockerfileConfig",
    "generate_dockerfile",
    # Deployers
    "LocalDeployer",
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
    # Env builder
    "DeployEnvBuilder",
    "build_deploy_env",
    "build_deploy_volumes",
    "build_stateful_service_env",
    "build_discovered_service_urls",
    "is_stateful_service",
    "get_service_container_port",
    "get_connection_info",
    "STATEFUL_SERVICE_TYPES",
    "get_stateful_service_types",
    "get_stateful_image",
    # Injection (auto-inject stateful service URLs)
    "DiscoveredService",
    "ServiceNeedingRedeploy",
    "ServiceDiscovery",
    "InjectionContext",
    "build_injection_env_vars",
    "get_env_var_name_for_service",
    "find_services_needing_redeploy",
    "format_redeploy_warning",
    # History
    "DeploymentHistory",
    "DeploymentRecord",
    "DeploymentStatus",
    "get_deployment_history",
    # Rollback
    "RollbackHelper",
    "RollbackMetadata",
    # Orchestration (background tasks)
    "DeployJobConfig",
    "deploy_task",
    "rollback_task",
    "stateful_deploy_task",
    "DEPLOY_TASKS",
]
