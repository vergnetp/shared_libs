"""
Deployment Context - Central context object for all infra operations.

Eliminates repetitive user/project/env parameters by providing a single
context object that carries all deployment state.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Any, Dict
import logging

if TYPE_CHECKING:
    from .storage.base import StorageBackend


@dataclass
class DeploymentContext:
    """
    Immutable context for deployment operations.
    
    Passed to all infra components, eliminating repetitive user/project params.
    
    Usage:
        # Create context once
        ctx = DeploymentContext(
            user_id="workspace_123",
            project_name="myapp",
            env="prod",
            storage=db_storage,
        )
        
        # Pass to components
        deployer = Deployer(ctx)
        deployer.deploy()
        
        # Create variant for different env
        uat_ctx = ctx.with_env("uat")
    """
    
    user_id: str
    """Tenant/workspace ID - identifies the owner of deployments."""
    
    project_name: str
    """Project name within the user's namespace."""
    
    env: str = "prod"
    """Environment: prod, uat, dev, staging, etc."""
    
    storage: Optional['StorageBackend'] = None
    """Pluggable storage backend for configs and state."""
    
    logger: Optional[logging.Logger] = None
    """Optional custom logger. If None, uses module logger."""
    
    # Optional overrides
    docker_hub_user: Optional[str] = None
    """Docker Hub username for image pushes."""
    
    default_zone: str = "lon1"
    """Default DigitalOcean zone for new servers."""
    
    dry_run: bool = False
    """If True, don't execute actual deployments (for testing)."""
    
    extra: Dict[str, Any] = field(default_factory=dict)
    """Additional context data for custom extensions."""
    
    def __post_init__(self):
        """Validate and normalize context."""
        if not self.user_id:
            raise ValueError("user_id is required")
        if not self.project_name:
            raise ValueError("project_name is required")
        
        # Normalize IDs
        self.user_id = str(self.user_id).strip()
        self.project_name = str(self.project_name).strip().lower()
        self.env = str(self.env).strip().lower()
        
        # Setup logger if not provided
        if self.logger is None:
            self.logger = logging.getLogger(f"infra.{self.namespace}")
    
    # =========================================================================
    # Derived Properties
    # =========================================================================
    
    @property
    def namespace(self) -> str:
        """
        Full namespace identifier: user_project_env
        
        Used for container names, volume names, network names, etc.
        """
        return f"{self.user_id}_{self.project_name}_{self.env}"
    
    @property
    def short_namespace(self) -> str:
        """
        Short namespace without env: user_project
        
        Used for project-level resources shared across envs.
        """
        return f"{self.user_id}_{self.project_name}"
    
    @property
    def container_prefix(self) -> str:
        """Prefix for Docker container names."""
        return self.namespace
    
    @property
    def network_name(self) -> str:
        """Docker network name for this deployment."""
        return f"{self.namespace}_network"
    
    @property
    def volume_prefix(self) -> str:
        """Prefix for Docker volume names."""
        return self.namespace
    
    # =========================================================================
    # Path Helpers
    # =========================================================================
    
    def local_path(self, *parts: str) -> str:
        """
        Get local storage path for this context.
        
        Example:
            ctx.local_path("config", "nginx.conf")
            # -> /local/user_id/project_name/env/config/nginx.conf
        """
        import os
        base = os.path.join("/local", self.user_id, self.project_name, self.env)
        if parts:
            return os.path.join(base, *parts)
        return base
    
    def remote_path(self, *parts: str) -> str:
        """
        Get remote server path for this context.
        
        Example:
            ctx.remote_path("data", "postgres")
            # -> /local/user_id/project_name/env/data/postgres
        """
        # Same structure locally and remotely
        return self.local_path(*parts)
    
    def secrets_path(self, filename: Optional[str] = None) -> str:
        """Get secrets directory or file path."""
        if filename:
            return self.local_path("secrets", filename)
        return self.local_path("secrets")
    
    def config_path(self, filename: Optional[str] = None) -> str:
        """Get config directory or file path."""
        if filename:
            return self.local_path("config", filename)
        return self.local_path("config")
    
    def data_path(self, service: Optional[str] = None) -> str:
        """Get data directory path, optionally for a specific service."""
        if service:
            return self.local_path("data", service)
        return self.local_path("data")
    
    def logs_path(self, service: Optional[str] = None) -> str:
        """Get logs directory path, optionally for a specific service."""
        if service:
            return self.local_path("logs", service)
        return self.local_path("logs")
    
    def backups_path(self, service: Optional[str] = None) -> str:
        """Get backups directory path, optionally for a specific service."""
        if service:
            return self.local_path("backups", service)
        return self.local_path("backups")
    
    # =========================================================================
    # Naming Helpers
    # =========================================================================
    
    def container_name(self, service: str, secondary: bool = False) -> str:
        """
        Get container name for a service.
        
        Args:
            service: Service name (e.g., "api", "postgres")
            secondary: If True, use secondary name for blue/green toggle
        
        Returns:
            Container name like "user_project_env_service" or "..._secondary"
        """
        name = f"{self.namespace}_{service}"
        if secondary:
            name = f"{name}_secondary"
        return name
    
    def volume_name(self, service: str, volume_type: str = "data") -> str:
        """
        Get volume name for a service.
        
        Args:
            service: Service name
            volume_type: Type of volume (data, config, logs, etc.)
        
        Returns:
            Volume name like "user_project_env_service_data"
        """
        return f"{self.namespace}_{service}_{volume_type}"
    
    def image_name(self, service: str, tag: str = "latest") -> str:
        """
        Get Docker image name for a service.
        
        Args:
            service: Service name
            tag: Image tag (default: latest)
        
        Returns:
            Full image name like "dockerhub_user/project_service:tag"
        """
        if self.docker_hub_user:
            return f"{self.docker_hub_user}/{self.project_name}_{service}:{tag}"
        return f"{self.project_name}_{service}:{tag}"
    
    # =========================================================================
    # Context Variants (Immutable)
    # =========================================================================
    
    def with_env(self, env: str) -> 'DeploymentContext':
        """
        Create new context with different environment.
        
        Original context is unchanged (immutable pattern).
        
        Example:
            prod_ctx = ctx.with_env("prod")
            uat_ctx = ctx.with_env("uat")
        """
        return DeploymentContext(
            user_id=self.user_id,
            project_name=self.project_name,
            env=env,
            storage=self.storage,
            logger=None,  # Will create new logger with new namespace
            docker_hub_user=self.docker_hub_user,
            default_zone=self.default_zone,
            dry_run=self.dry_run,
            extra=self.extra.copy(),
        )
    
    def with_project(self, project_name: str) -> 'DeploymentContext':
        """Create new context with different project."""
        return DeploymentContext(
            user_id=self.user_id,
            project_name=project_name,
            env=self.env,
            storage=self.storage,
            logger=None,
            docker_hub_user=self.docker_hub_user,
            default_zone=self.default_zone,
            dry_run=self.dry_run,
            extra=self.extra.copy(),
        )
    
    def with_storage(self, storage: 'StorageBackend') -> 'DeploymentContext':
        """Create new context with different storage backend."""
        return DeploymentContext(
            user_id=self.user_id,
            project_name=self.project_name,
            env=self.env,
            storage=storage,
            logger=self.logger,
            docker_hub_user=self.docker_hub_user,
            default_zone=self.default_zone,
            dry_run=self.dry_run,
            extra=self.extra.copy(),
        )
    
    def with_dry_run(self, dry_run: bool = True) -> 'DeploymentContext':
        """Create new context with dry_run flag."""
        return DeploymentContext(
            user_id=self.user_id,
            project_name=self.project_name,
            env=self.env,
            storage=self.storage,
            logger=self.logger,
            docker_hub_user=self.docker_hub_user,
            default_zone=self.default_zone,
            dry_run=dry_run,
            extra=self.extra.copy(),
        )
    
    # =========================================================================
    # Logging Shortcuts
    # =========================================================================
    
    def log(self, msg: str, level: str = "info", **extra):
        """Log with context info automatically included."""
        log_func = getattr(self.logger, level, self.logger.info)
        log_func(msg, extra={"ctx": self.namespace, **extra})
    
    def log_info(self, msg: str, **extra):
        self.log(msg, "info", **extra)
    
    def log_warning(self, msg: str, **extra):
        self.log(msg, "warning", **extra)
    
    def log_error(self, msg: str, **extra):
        self.log(msg, "error", **extra)
    
    def log_debug(self, msg: str, **extra):
        self.log(msg, "debug", **extra)
    
    # =========================================================================
    # Representation
    # =========================================================================
    
    def __repr__(self) -> str:
        return f"DeploymentContext({self.namespace})"
    
    def __str__(self) -> str:
        return self.namespace
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary (for serialization/logging)."""
        return {
            "user_id": self.user_id,
            "project_name": self.project_name,
            "env": self.env,
            "namespace": self.namespace,
            "docker_hub_user": self.docker_hub_user,
            "default_zone": self.default_zone,
            "dry_run": self.dry_run,
        }


# Convenience alias
Context = DeploymentContext
