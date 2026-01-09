"""
Volume Manager - Standard paths and volume management for deployments.

Provides consistent volume path generation and directory management.
"""

from dataclasses import dataclass
from typing import List, Optional, Literal
from pathlib import Path


VolumeType = Literal["data", "config", "secrets", "logs", "backups"]


@dataclass
class VolumeMount:
    """Represents a volume mount configuration."""
    host_path: str
    container_path: str
    readonly: bool = False
    
    def to_docker(self) -> str:
        """Convert to Docker -v format."""
        mount = f"{self.host_path}:{self.container_path}"
        if self.readonly:
            mount += ":ro"
        return mount


class VolumeManager:
    """
    Manage volume paths for deployments.
    
    Standard directory structure on host:
        /data/{user}/{project}/{env}/{service}/data/     - persistent data (SQLite, uploads)
        /data/{user}/{project}/{env}/{service}/config/   - configuration files
        /data/{user}/{project}/{env}/{service}/secrets/  - secret files (passwords)
        /data/{user}/{project}/{env}/{service}/logs/     - log files
        /data/{user}/{project}/{env}/{service}/backups/  - backup files
    
    Standard container paths:
        /app/data      - application data
        /app/config    - configuration
        /app/secrets   - secrets (read-only)
        /app/logs      - logs
    
    Service-specific container paths:
        postgres: /var/lib/postgresql/data
        mysql:    /var/lib/mysql
        redis:    /data
        mongo:    /data/db
    
    Usage:
        vm = VolumeManager(base_path="/data")
        
        # Get standard volume mount
        mount = vm.get_volume_mount("u1", "myapp", "prod", "api", "data")
        # -> VolumeMount("/data/u1/myapp/prod/api/data", "/app/data")
        
        # Get all volumes for a service
        volumes = vm.get_service_volumes("u1", "myapp", "prod", "postgres")
        # -> [VolumeMount for data, secrets]
    """
    
    # Base path for all volumes on host
    DEFAULT_BASE_PATH = "/data"
    
    # Standard container paths by service type
    SERVICE_DATA_PATHS = {
        "postgres": "/var/lib/postgresql/data",
        "mysql": "/var/lib/mysql",
        "mariadb": "/var/lib/mysql",
        "redis": "/data",
        "mongo": "/data/db",
        "mongodb": "/data/db",
        "opensearch": "/usr/share/opensearch/data",
        "elasticsearch": "/usr/share/elasticsearch/data",
    }
    
    # Standard container paths by volume type (for custom services)
    VOLUME_TYPE_PATHS = {
        "data": "/app/data",
        "config": "/app/config",
        "secrets": "/app/secrets",
        "logs": "/app/logs",
        "backups": "/app/backups",
    }
    
    def __init__(self, base_path: str = None):
        self.base_path = base_path or self.DEFAULT_BASE_PATH
    
    def get_host_path(
        self,
        user: str,
        project: str,
        env: str,
        service: str,
        volume_type: VolumeType,
    ) -> str:
        """
        Get host path for a volume.
        
        Args:
            user: User ID
            project: Project name
            env: Environment (prod, staging, dev)
            service: Service name
            volume_type: Type of volume
            
        Returns:
            Host path string
        """
        return f"{self.base_path}/{user}/{project}/{env}/{service}/{volume_type}"
    
    def get_container_path(self, service: str, volume_type: VolumeType) -> str:
        """
        Get container path for a volume.
        
        Uses service-specific paths for known services (postgres, redis, etc.)
        Falls back to standard /app/{type} paths for custom services.
        
        Args:
            service: Service name
            volume_type: Type of volume
            
        Returns:
            Container path string
        """
        service_lower = service.lower()
        
        # Data volumes use service-specific paths
        if volume_type == "data" and service_lower in self.SERVICE_DATA_PATHS:
            return self.SERVICE_DATA_PATHS[service_lower]
        
        # Everything else uses standard paths
        return self.VOLUME_TYPE_PATHS.get(volume_type, f"/app/{volume_type}")
    
    def get_volume_mount(
        self,
        user: str,
        project: str,
        env: str,
        service: str,
        volume_type: VolumeType,
        readonly: bool = None,
    ) -> VolumeMount:
        """
        Get a VolumeMount for a specific volume type.
        
        Args:
            user: User ID
            project: Project name
            env: Environment
            service: Service name
            volume_type: Type of volume
            readonly: Override default readonly setting (secrets are readonly by default)
            
        Returns:
            VolumeMount object
        """
        host_path = self.get_host_path(user, project, env, service, volume_type)
        container_path = self.get_container_path(service, volume_type)
        
        # Secrets are readonly by default
        if readonly is None:
            readonly = (volume_type == "secrets")
        
        return VolumeMount(
            host_path=host_path,
            container_path=container_path,
            readonly=readonly,
        )
    
    def get_service_volumes(
        self,
        user: str,
        project: str,
        env: str,
        service: str,
        include_data: bool = True,
        include_config: bool = False,
        include_secrets: bool = False,
        include_logs: bool = False,
    ) -> List[VolumeMount]:
        """
        Get all volume mounts for a service.
        
        Args:
            user: User ID
            project: Project name
            env: Environment
            service: Service name
            include_*: Which volume types to include
            
        Returns:
            List of VolumeMount objects
        """
        volumes = []
        
        if include_data:
            volumes.append(self.get_volume_mount(user, project, env, service, "data"))
        if include_config:
            volumes.append(self.get_volume_mount(user, project, env, service, "config"))
        if include_secrets:
            volumes.append(self.get_volume_mount(user, project, env, service, "secrets"))
        if include_logs:
            volumes.append(self.get_volume_mount(user, project, env, service, "logs"))
        
        return volumes
    
    def get_standard_service_volumes(
        self,
        user: str,
        project: str,
        env: str,
        service: str,
    ) -> List[VolumeMount]:
        """
        Get standard volumes for known service types.
        
        Automatically includes appropriate volumes based on service type:
        - postgres/mysql: data + secrets
        - redis: data + secrets
        - api/backend: data (for SQLite/uploads)
        - frontend/web: (none typically)
        
        Args:
            user: User ID
            project: Project name
            env: Environment
            service: Service name
            
        Returns:
            List of VolumeMount objects
        """
        service_lower = service.lower()
        
        # Database services need data + secrets
        if service_lower in ("postgres", "postgresql", "mysql", "mariadb", "mongo", "mongodb"):
            return self.get_service_volumes(
                user, project, env, service,
                include_data=True,
                include_secrets=True,
            )
        
        # Cache services need data + secrets
        if service_lower in ("redis", "memcached"):
            return self.get_service_volumes(
                user, project, env, service,
                include_data=True,
                include_secrets=True,
            )
        
        # Search services need data
        if service_lower in ("opensearch", "elasticsearch"):
            return self.get_service_volumes(
                user, project, env, service,
                include_data=True,
            )
        
        # API/backend services may need data for SQLite/uploads
        if service_lower in ("api", "backend", "server", "app"):
            return self.get_service_volumes(
                user, project, env, service,
                include_data=True,
            )
        
        # Default: just data
        return self.get_service_volumes(
            user, project, env, service,
            include_data=True,
        )
    
    def to_docker_volumes(self, volumes: List[VolumeMount]) -> List[str]:
        """
        Convert VolumeMount list to Docker -v format strings.
        
        Args:
            volumes: List of VolumeMount objects
            
        Returns:
            List of Docker volume strings
        """
        return [v.to_docker() for v in volumes]


# Convenience function for quick access
def get_volume_path(
    user: str,
    project: str,
    env: str,
    service: str,
    volume_type: VolumeType,
    base_path: str = "/data",
) -> str:
    """Quick helper to get a host volume path."""
    return f"{base_path}/{user}/{project}/{env}/{service}/{volume_type}"
