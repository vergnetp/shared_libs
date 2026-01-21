"""
Infra - Multi-tenant Docker Deployment System

A clean, context-driven deployment system for Docker containers.

Quick Start:
    from backend.infra import DeploymentContext, DeployService
    
    ctx = DeploymentContext(
        user_id="workspace_123",
        project_name="myapp",
        env="prod",
        storage=storage_backend,
    )
    
    service = DeployService(do_token="...", cf_token="...")
    result = await service.deploy(...)

Components:
    - Context: DeploymentContext for all operations
    - Storage: FileStorageBackend, DatabaseStorageBackend
    - Core: Service definitions, Result types
    - Cloud: DOClient, ServerManager (DigitalOcean), CloudflareClient
    - Networking: NginxConfigGenerator (SSL via Cloudflare proxy)
    - Monitoring: HealthChecker, HealthAggregator
    - Scheduling: TaskScheduler with handlers
    - Node Agent: NodeAgentClient for SSH-free deployments (SaaS-ready)
    
All remote operations go through NodeAgentClient - no direct SSH/Docker connections.
"""

__version__ = "2.1.0"

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

# Cloud Providers (extended clients with infra-specific methods)
from .providers import (
    # DigitalOcean
    DOClient, AsyncDOClient, DOAPIError, DOError, Droplet, Result, MANAGED_TAG, ServerManager,
    # Cloudflare
    CloudflareClient, AsyncCloudflareClient, CloudflareError, DNSRecord,
    # Cloud-init
    CloudInitConfig, build_cloudinit_script, SNAPSHOT_PRESETS,
    # Snapshot service
    SnapshotService, AsyncSnapshotService, SnapshotConfig,
)

# Networking
from .networking import (
    NginxConfigGenerator,
    ServerBlock,
    Location,
    Upstream,
    Backend,
    LoadBalanceMethod,
)

# Monitoring
from .monitoring import (
    HealthChecker,
    HealthAggregator,
    HealthCheckResult,
    ServiceHealth,
    HealthStatus,
)

# Scheduling
from .scheduling import (
    TaskScheduler,
    ScheduledTask,
    TaskType,
    TaskStatus,
    get_scheduler,
    register_all_handlers,
    TASK_HANDLERS,
)

# Naming utilities
from .utils import (
    DONaming,
    DeploymentNaming,
    sanitize_for_dns,
    sanitize_for_tag,
    sanitize_for_docker,
    generate_friendly_name,
)

# Deploy (MVP)
from .deploy import (
    LocalDeployer,
    DeployConfig,
    DeployResult as LocalDeployResult,
    DockerfileGenerator,
    DockerfileConfig,
)

# Node Agent (SSH-free deployments)
from .node_agent import (
    NODE_AGENT_CODE,
    NodeAgentClient,
)

# Architecture (topology discovery)
from .architecture import (
    ArchitectureService,
    AsyncArchitectureService,
    ArchitectureTopology,
    ServiceNode,
    ServiceEdge,
    ServerStatus,
    InfrastructureComponent,
)

# Provisioning (server lifecycle)
from .provisioning import (
    ProvisioningService,
    AsyncProvisioningService,
    ProvisionRequest,
    ProvisionResult,
)

# Fleet (health monitoring)
from .fleet import (
    FleetService,
    AsyncFleetService,
    ServerHealth,
    FleetHealth,
)

# DNS (cleanup utilities)
from .dns import (
    DnsCleanupService,
    AsyncDnsCleanupService,
    DnsCleanupResult,
)

# Registry (service mesh)
from .registry import (
    ServiceRegistry,
    AsyncServiceRegistry,
    ServiceRecord,
)

# Streaming (SSE utilities)
from .streaming import (
    SSEEmitter,
    SSEEvent,
    DeploymentEmitter,
    sse_response,
    run_in_thread,
)

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
    
    # Cloud Providers
    "DOClient",
    "AsyncDOClient",
    "DOAPIError",
    "DOError",
    "Droplet",
    "Result",
    "MANAGED_TAG",
    "ServerManager",
    "CloudflareClient",
    "AsyncCloudflareClient",
    "CloudflareError",
    "DNSRecord",
    "CloudInitConfig",
    "build_cloudinit_script",
    "SNAPSHOT_PRESETS",
    "SnapshotService",
    "AsyncSnapshotService",
    "SnapshotConfig",
    
    # Networking
    "NginxConfigGenerator",
    "ServerBlock",
    "Location",
    "Upstream",
    "Backend",
    "LoadBalanceMethod",
    
    # Monitoring
    "HealthChecker",
    "HealthAggregator",
    "HealthCheckResult",
    "ServiceHealth",
    "HealthStatus",
    
    # Scheduling
    "TaskScheduler",
    "ScheduledTask",
    "TaskType",
    "TaskStatus",
    "get_scheduler",
    "register_all_handlers",
    "TASK_HANDLERS",
    
    # Naming
    "DONaming",
    "DeploymentNaming",
    "sanitize_for_dns",
    "sanitize_for_tag",
    "sanitize_for_docker",
    "generate_friendly_name",
    
    # Deploy (MVP)
    "LocalDeployer",
    "DeployConfig",
    "LocalDeployResult",
    "DockerfileGenerator",
    "DockerfileConfig",
    
    # Node Agent
    "NODE_AGENT_CODE",
    "NodeAgentClient",
    
    # Architecture
    "ArchitectureService",
    "AsyncArchitectureService",
    "ArchitectureTopology",
    "ServiceNode",
    "ServiceEdge",
    "ServerStatus",
    "InfrastructureComponent",
    
    # Provisioning
    "ProvisioningService",
    "AsyncProvisioningService",
    "ProvisionRequest",
    "ProvisionResult",
    
    # Fleet
    "FleetService",
    "AsyncFleetService",
    "ServerHealth",
    "FleetHealth",
    
    # DNS
    "DnsCleanupService",
    "AsyncDnsCleanupService",
    "DnsCleanupResult",
    
    # Registry
    "ServiceRegistry",
    "AsyncServiceRegistry",
    "ServiceRecord",
    
    # Streaming
    "SSEEmitter",
    "SSEEvent",
    "DeploymentEmitter",
    "sse_response",
    "run_in_thread",
]
