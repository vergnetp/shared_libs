"""
Service model - defines a deployable service.

Clean dataclass representing service configuration.
No deployment logic - just data.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum


class ServiceType(Enum):
    """Type of service."""
    CUSTOM = "custom"           # Custom Dockerfile
    PYTHON = "python"           # Python app
    NODE = "node"               # Node.js app
    REACT = "react"             # React frontend (static)
    POSTGRES = "postgres"       # PostgreSQL database
    REDIS = "redis"             # Redis cache
    OPENSEARCH = "opensearch"   # OpenSearch
    NGINX = "nginx"             # Nginx proxy


class RestartPolicy(Enum):
    """Container restart policy."""
    NO = "no"
    ALWAYS = "always"
    ON_FAILURE = "on-failure"
    UNLESS_STOPPED = "unless-stopped"


@dataclass
class ServicePort:
    """Port mapping for a service."""
    container_port: int          # Port inside container
    host_port: Optional[int] = None  # Port on host (None = auto)
    protocol: str = "tcp"        # tcp or udp
    public: bool = False         # Expose to internet?


@dataclass
class ServiceVolume:
    """Volume mount for a service."""
    name: str                    # Volume name or host path
    container_path: str          # Path inside container
    read_only: bool = False
    type: str = "volume"         # "volume" or "bind"


@dataclass
class ServiceHealthCheck:
    """Health check configuration."""
    type: str = "http"           # http, tcp, exec
    path: str = "/health"        # HTTP path (for http type)
    port: Optional[int] = None   # Port to check (default: main port)
    interval: int = 30           # Seconds between checks
    timeout: int = 10            # Seconds to wait for response
    retries: int = 3             # Failures before unhealthy
    start_period: int = 60       # Grace period on start


@dataclass
class Service:
    """
    Deployable service definition.
    
    Represents a single containerized service with all its configuration.
    """
    
    name: str
    """Unique service name within project."""
    
    type: ServiceType = ServiceType.CUSTOM
    """Service type - determines default configuration."""
    
    # Image configuration
    image: Optional[str] = None
    """Docker image (e.g., "postgres:15"). If None, builds from dockerfile."""
    
    dockerfile: Optional[str] = None
    """Dockerfile content or path. Required if no image specified."""
    
    build_context: Optional[str] = None
    """Build context directory (for custom builds)."""
    
    # Git source (for building from repo)
    git_repo: Optional[str] = None
    """Git repository URL (e.g., "https://github.com/user/repo.git@main")."""
    
    git_branch: Optional[str] = None
    """Git branch (if not in repo URL)."""
    
    # Runtime configuration  
    command: Optional[str] = None
    """Override container command."""
    
    entrypoint: Optional[str] = None
    """Override container entrypoint."""
    
    environment: Dict[str, str] = field(default_factory=dict)
    """Environment variables."""
    
    secrets: List[str] = field(default_factory=list)
    """Secret names to inject (resolved at deploy time)."""
    
    # Networking
    ports: List[ServicePort] = field(default_factory=list)
    """Port mappings."""
    
    domain: Optional[str] = None
    """Public domain (triggers SSL setup)."""
    
    internal: bool = True
    """Only accessible within network (not public)."""
    
    # Storage
    volumes: List[ServiceVolume] = field(default_factory=list)
    """Volume mounts."""
    
    # Resources
    memory: Optional[str] = None
    """Memory limit (e.g., "512m", "2g")."""
    
    cpus: Optional[float] = None
    """CPU limit (e.g., 0.5, 2.0)."""
    
    # Deployment
    replicas: int = 1
    """Number of container instances."""
    
    servers_count: int = 1
    """Number of servers to deploy to."""
    
    zone: str = "lon1"
    """Cloud zone/region."""
    
    # Dependencies
    depends_on: List[str] = field(default_factory=list)
    """Services that must start before this one."""
    
    startup_order: int = 0
    """Explicit startup order (lower = earlier)."""
    
    # Health & lifecycle
    health_check: Optional[ServiceHealthCheck] = None
    """Health check configuration."""
    
    restart_policy: RestartPolicy = RestartPolicy.UNLESS_STOPPED
    """Container restart policy."""
    
    # Scheduling (for cron-like services)
    schedule: Optional[str] = None
    """Cron schedule (e.g., "0 * * * *"). If set, runs as scheduled job."""
    
    # Labels and metadata
    labels: Dict[str, str] = field(default_factory=dict)
    """Container labels."""
    
    def __post_init__(self):
        """Apply type-specific defaults."""
        if self.type == ServiceType.POSTGRES:
            self._apply_postgres_defaults()
        elif self.type == ServiceType.REDIS:
            self._apply_redis_defaults()
        elif self.type == ServiceType.PYTHON:
            self._apply_python_defaults()
        elif self.type == ServiceType.REACT:
            self._apply_react_defaults()
    
    def _apply_postgres_defaults(self):
        if not self.image:
            self.image = "postgres:15-alpine"
        if not self.ports:
            self.ports = [ServicePort(container_port=5432)]
        if not self.volumes:
            self.volumes = [ServiceVolume(
                name=f"{self.name}_data",
                container_path="/var/lib/postgresql/data",
            )]
        if not self.health_check:
            self.health_check = ServiceHealthCheck(
                type="exec",
                path="pg_isready -U postgres",
            )
    
    def _apply_redis_defaults(self):
        if not self.image:
            self.image = "redis:7-alpine"
        if not self.ports:
            self.ports = [ServicePort(container_port=6379)]
        if not self.health_check:
            self.health_check = ServiceHealthCheck(
                type="tcp",
                port=6379,
            )
    
    def _apply_python_defaults(self):
        if not self.ports:
            self.ports = [ServicePort(container_port=8000)]
        if not self.health_check:
            self.health_check = ServiceHealthCheck(
                type="http",
                path="/health",
                port=8000,
            )
    
    def _apply_react_defaults(self):
        if not self.ports:
            self.ports = [ServicePort(container_port=80, public=True)]
    
    @property
    def main_port(self) -> Optional[int]:
        """Get the primary container port."""
        return self.ports[0].container_port if self.ports else None
    
    @property
    def needs_build(self) -> bool:
        """Check if this service needs to be built (vs pulled)."""
        return self.image is None or self.dockerfile is not None or self.git_repo is not None
    
    @classmethod
    def postgres(cls, name: str = "postgres", **kwargs) -> 'Service':
        """Create PostgreSQL service with sensible defaults."""
        return cls(name=name, type=ServiceType.POSTGRES, **kwargs)
    
    @classmethod
    def redis(cls, name: str = "redis", **kwargs) -> 'Service':
        """Create Redis service with sensible defaults."""
        return cls(name=name, type=ServiceType.REDIS, **kwargs)
    
    @classmethod
    def python(cls, name: str, **kwargs) -> 'Service':
        """Create Python service."""
        return cls(name=name, type=ServiceType.PYTHON, **kwargs)
    
    @classmethod  
    def from_dict(cls, data: Dict[str, Any]) -> 'Service':
        """Create service from dictionary config."""
        # Convert nested dicts to dataclasses
        if "ports" in data:
            data["ports"] = [
                ServicePort(**p) if isinstance(p, dict) else p 
                for p in data["ports"]
            ]
        if "volumes" in data:
            data["volumes"] = [
                ServiceVolume(**v) if isinstance(v, dict) else v
                for v in data["volumes"]
            ]
        if "health_check" in data and isinstance(data["health_check"], dict):
            data["health_check"] = ServiceHealthCheck(**data["health_check"])
        if "type" in data and isinstance(data["type"], str):
            data["type"] = ServiceType(data["type"])
        if "restart_policy" in data and isinstance(data["restart_policy"], str):
            data["restart_policy"] = RestartPolicy(data["restart_policy"])
        
        return cls(**data)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "type": self.type.value,
            "image": self.image,
            "dockerfile": self.dockerfile,
            "build_context": self.build_context,
            "git_repo": self.git_repo,
            "git_branch": self.git_branch,
            "command": self.command,
            "entrypoint": self.entrypoint,
            "environment": self.environment,
            "secrets": self.secrets,
            "ports": [{"container_port": p.container_port, "host_port": p.host_port, "protocol": p.protocol, "public": p.public} for p in self.ports],
            "domain": self.domain,
            "internal": self.internal,
            "volumes": [{"name": v.name, "container_path": v.container_path, "read_only": v.read_only, "type": v.type} for v in self.volumes],
            "memory": self.memory,
            "cpus": self.cpus,
            "replicas": self.replicas,
            "servers_count": self.servers_count,
            "zone": self.zone,
            "depends_on": self.depends_on,
            "startup_order": self.startup_order,
            "health_check": {
                "type": self.health_check.type,
                "path": self.health_check.path,
                "port": self.health_check.port,
                "interval": self.health_check.interval,
                "timeout": self.health_check.timeout,
                "retries": self.health_check.retries,
                "start_period": self.health_check.start_period,
            } if self.health_check else None,
            "restart_policy": self.restart_policy.value,
            "schedule": self.schedule,
            "labels": self.labels,
        }
