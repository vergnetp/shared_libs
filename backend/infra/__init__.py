"""
Infra - Multi-tenant Docker Deployment System

A clean, context-driven deployment system for Docker containers.

Quick Start:
    from backend.infra import DeploymentContext, Deployer, Service
    
    ctx = DeploymentContext(
        user_id="workspace_123",
        project_name="myapp",
        env="prod",
        storage=storage_backend,
    )
    
    deployer = Deployer(ctx)
    result = deployer.deploy()

Components:
    - Context: DeploymentContext for all operations
    - Storage: FileStorageBackend, DatabaseStorageBackend
    - Core: Deployer, Service, Result types
    - Docker: DockerClient, ImageBuilder
    - SSH: SSHClient for remote operations
    - Cloud: DOClient, ServerManager (DigitalOcean)
    - Networking: NginxConfigGenerator, SSLManager
    - Monitoring: HealthChecker, HealthAggregator
    - Scheduling: Scheduler, BackupManager
    - Node Agent: NodeAgentClient for SSH-free deployments (SaaS-ready)
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
from .docker import DockerClient, Container, ImageBuilder, BuildConfig

# SSH
from .ssh import SSHClient, SSHConfig

# Cloud
from .cloud import DOClient, DOAPIError, Droplet, ServerManager

# Networking
from .networking import (
    NginxConfigGenerator,
    ServerBlock,
    Location,
    Upstream,
    Backend,
    LoadBalanceMethod,
    SSLManager,
    Certificate,
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
    Scheduler,
    CronJob,
    ScheduledTask,
    ScheduleFrequency,
    BackupManager,
    BackupConfig,
    BackupResult,
    BackupType,
    StorageType,
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
    RemoteDeployer,
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
    "ImageBuilder",
    "BuildConfig",
    
    # SSH
    "SSHClient",
    "SSHConfig",
    
    # Cloud
    "DOClient",
    "DOAPIError",
    "Droplet",
    "ServerManager",
    
    # Networking
    "NginxConfigGenerator",
    "ServerBlock",
    "Location",
    "Upstream",
    "Backend",
    "LoadBalanceMethod",
    "SSLManager",
    "Certificate",
    
    # Monitoring
    "HealthChecker",
    "HealthAggregator",
    "HealthCheckResult",
    "ServiceHealth",
    "HealthStatus",
    
    # Scheduling
    "Scheduler",
    "CronJob",
    "ScheduledTask",
    "ScheduleFrequency",
    "BackupManager",
    "BackupConfig",
    "BackupResult",
    "BackupType",
    "StorageType",
    
    # Naming
    "DONaming",
    "DeploymentNaming",
    "sanitize_for_dns",
    "sanitize_for_tag",
    "sanitize_for_docker",
    "generate_friendly_name",
    
    # Deploy (MVP)
    "LocalDeployer",
    "RemoteDeployer",
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
