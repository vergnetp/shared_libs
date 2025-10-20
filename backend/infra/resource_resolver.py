"""
Resource Resolver - Unified API for all deployment resource resolution.

This module provides a single entry point (facade) for resolving all resource identifiers
and paths in the deployment system. It delegates to specialized resolvers while providing
a clean, consistent API.

Design Pattern: Facade
- Simplifies complex subsystem interactions
- Provides high-level convenience methods
- Maintains separation of concerns in implementation
"""

import os
import hashlib
from pathlib import Path
from typing import Optional, Literal

try:
    from deployment_naming import DeploymentNaming
    from deployment_port_resolver import DeploymentPortResolver
    from path_resolver import PathResolver
except ImportError:
    # Fallback for relative imports
    from .deployment_naming import DeploymentNaming
    from .deployment_port_resolver import DeploymentPortResolver
    from .path_resolver import PathResolver


class ResourceResolver:
    """
    Unified facade for all resource resolution in deployment system.
    
    This is the SINGLE entry point that application code should use.
    All resource identifiers (names, ports, paths, credentials) are resolved here.
    
    Architecture:
        ResourceResolver (Facade)
            ├─→ DeploymentNaming (container/image/network names)
            ├─→ PathResolver (volume paths, OS detection)
            └─→ DeploymentPortResolver (port generation)
    
    Categories:
        1. Naming: Container, image, network names
        2. Generic Services: Password, host, port (works for all services)
        3. Database-Specific: DB name, user, connection strings
        4. Paths: Volume mount paths (host and container)
        5. Ports: Host and internal service discovery ports
    """
    
    @staticmethod
    def _get_secret_filename(service: str) -> str:
        """
        Get the secret filename for a service.
        
        Simple pattern: {service}_password
        
        Args:
            service: Service name (postgres, redis, mongo, opensearch, etc.)
        
        Returns:
            Secret filename (without path)
        
        Examples:
            >>> ResourceResolver._get_secret_filename("postgres")
            'postgres_password'
            >>> ResourceResolver._get_secret_filename("redis")
            'redis_password'
            >>> ResourceResolver._get_secret_filename("opensearch")
            'opensearch_password'
        """
        return f"{service}_password"
    
    # ========================================
    # NAMING RESOURCES
    # ========================================
    
    @staticmethod
    def get_container_name(project: str, env: str, service: str) -> str:
        """
        Get Docker container name for a service.
        
        Format: {project}_{env}_{service}
        
        Args:
            project: Project name
            env: Environment name
            service: Service name
        
        Returns:
            Container name string
        
        Examples:
            >>> ResourceResolver.get_container_name("myapp", "prod", "api")
            'myapp_prod_api'
        """
        return DeploymentNaming.get_container_name(project, env, service)
    
    @staticmethod
    def get_container_name_pattern(project: str, env: str, service: str) -> str:
        """
        Get wildcard pattern for finding service containers (primary and secondary).
        
        Format: {project}_{env}_{service}*
        
        Matches both primary and secondary containers in toggle deployments.
        
        Args:
            project: Project name
            env: Environment name
            service: Service name
        
        Returns:
            Pattern string for container matching
        
        Examples:
            >>> ResourceResolver.get_container_name_pattern("myapp", "prod", "api")
            'myapp_prod_api*'
        """
        return DeploymentNaming.get_container_name_pattern(project, env, service)
    
    @staticmethod
    def get_image_name(docker_hub_user: str, project: str, env: str, 
                      service: str, version: str = "latest") -> str:
        """
        Get Docker image name for registry.
        
        Format: {docker_hub_user}/{project}-{env}-{service}:{version}
        
        Args:
            docker_hub_user: Docker Hub username
            project: Project name
            env: Environment name
            service: Service name
            version: Image version tag (default: "latest")
        
        Returns:
            Docker image name with tag
        
        Examples:
            >>> ResourceResolver.get_image_name("alice", "myapp", "prod", "api", "1.2.3")
            'alice/myapp-prod-api:1.2.3'
        """
        return DeploymentNaming.get_image_name(docker_hub_user, project, env, service, version)
    
    @staticmethod
    def get_network_name(project: str, env: str) -> str:
        """
        Get Docker network name for project/environment.
        
        Format: {project}_{env}_network
        
        Args:
            project: Project name
            env: Environment name
        
        Returns:
            Docker network name
        
        Examples:
            >>> ResourceResolver.get_network_name("myapp", "prod")
            'myapp_prod_network'
        """
        return DeploymentNaming.get_network_name(project, env)
    
    @staticmethod
    def get_dockerfile_name(project: str, env: str, service: str) -> str:
        """
        Get Dockerfile name with project/env/service discrimination.
        
        Format: Dockerfile.{project}-{env}-{service}
        
        Args:
            project: Project name
            env: Environment name
            service: Service name
        
        Returns:
            Dockerfile filename
        
        Examples:
            >>> ResourceResolver.get_dockerfile_name("myapp", "prod", "api")
            'Dockerfile.myapp-prod-api'
        """
        return DeploymentNaming.get_dockerfile_name(project, env, service)
    
    @staticmethod
    def get_nginx_config_name(project: str, env: str, service: str) -> str:
        """
        Get nginx configuration filename for a service.
        
        Format: nginx-{project}_{env}_{service}.conf
        
        Args:
            project: Project name
            env: Environment name
            service: Service name
        
        Returns:
            Nginx config filename
        
        Examples:
            >>> ResourceResolver.get_nginx_config_name("myapp", "prod", "api")
            'nginx-myapp_prod_api.conf'
        """
        return DeploymentNaming.get_nginx_config_name(project, env, service)
    
    # ========================================
    # GENERIC SERVICE RESOURCES
    # ========================================
    
    @staticmethod
    def get_service_password(project: str, env: str, service: str) -> str:
        """
        Read service password from secrets file (GENERIC for all services).
        
        Delegates ALL path resolution to PathResolver - no hardcoded paths.
        
        Works for: postgres, mysql, redis, mongo, opensearch, elasticsearch, etc.
        
        Strategy:
        1. Check environment variable {SERVICE}_PASSWORD_FILE for custom path
        2. Try container path (via PathResolver.get_volume_container_path)
        3. Try host path (via PathResolver.get_volume_host_path)
        4. Fall back to {SERVICE}_PASSWORD environment variable
        
        Args:
            project: Project name
            env: Environment name
            service: Service name (postgres, redis, mongo, opensearch, etc.)
        
        Returns:
            Password string, or empty string if not found
        
        Examples:
            >>> ResourceResolver.get_service_password("myapp", "prod", "postgres")
            'dbpassword123'
            >>> ResourceResolver.get_service_password("myapp", "prod", "redis")
            'redispassword456'
            >>> ResourceResolver.get_service_password("myapp", "prod", "opensearch")
            'opensearchpassword789'
        """
        secret_filename = ResourceResolver._get_secret_filename(service)
        env_var_name = f"{service.upper()}_PASSWORD_FILE"
        env_var_password = f"{service.upper()}_PASSWORD"
        
        # 1. Custom path from environment variable
        custom_path = os.getenv(env_var_name)
        if custom_path and os.path.exists(custom_path):
            return Path(custom_path).read_text().strip()
        
        # 2. Container path (PathResolver determines the actual path)
        try:
            container_path = PathResolver.get_volume_container_path(service, "secrets")
            password_file = Path(container_path) / secret_filename
            if password_file.exists():
                return password_file.read_text().strip()
        except Exception:
            pass
        
        # 3. Host path (PathResolver determines OS-aware path)
        try:
            host_path = PathResolver.get_volume_host_path(
                project, env, service, "secrets", "localhost"
            )
            password_file = Path(host_path) / secret_filename
            if password_file.exists():
                return password_file.read_text().strip()
        except Exception:
            pass
        
        # 4. Fallback to environment variable
        return os.getenv(env_var_password, "")
    
    @staticmethod
    def get_service_port(project: str, env: str, service: str) -> int:
        """
        Get service internal port for service discovery (GENERIC for all services).
        
        This is the stable localhost port that applications use to connect.
        The port is deterministically generated from project/env/service hash.
        
        Works for: postgres, redis, mongo, opensearch, api, worker, etc.
        
        Args:
            project: Project name
            env: Environment name
            service: Service name
        
        Returns:
            Internal port number (5000-65535 range)
        
        Examples:
            >>> ResourceResolver.get_service_port("myapp", "prod", "postgres")
            5234
            >>> ResourceResolver.get_service_port("myapp", "prod", "redis")
            6891
            >>> ResourceResolver.get_service_port("myapp", "prod", "opensearch")
            9456
        """
        return DeploymentPortResolver.get_internal_port(project, env, service)
    
    @staticmethod
    def get_service_host(project: str, env: str, service: str) -> str:
        """
        Get service host for application connections (GENERIC for all services).
        
        Always returns "nginx" as applications connect via nginx proxy
        on the internal service discovery port.
        
        Args:
            project: Project name (unused, for API consistency)
            env: Environment name (unused, for API consistency)
            service: Service name (unused, for API consistency)
        
        Returns:
            "localhost" string
        
        Examples:
            >>> ResourceResolver.get_service_host("myapp", "prod", "postgres")
            'nginx'
            >>> ResourceResolver.get_service_host("myapp", "prod", "opensearch")
            'nginx'
        """
        return "nginx"
    
    # ========================================
    # DATABASE-SPECIFIC RESOURCES
    # ========================================
    
    @staticmethod
    def get_db_name(project: str, env: str, service: str = "postgres") -> str:
        """
        Generate database name from project/env/service.
        
        Uses MD5 hash to ensure uniqueness and consistent length.
        Format: {project}_{hash8}
        
        Args:
            project: Project name
            env: Environment name
            service: Database service name (default: "postgres")
        
        Returns:
            Database name string
        
        Examples:
            >>> ResourceResolver.get_db_name("myapp", "prod", "postgres")
            'myapp_8e9fb088'
        """
        hash_input = f"{project}_{env}_{service}"
        db_suffix = hashlib.md5(hash_input.encode()).hexdigest()[:8]
        return f"{project}_{db_suffix}"
    
    @staticmethod
    def get_db_user(project: str, service: str = "postgres") -> str:
        """
        Generate database username from project.
        
        Format: {project}_user
        
        Args:
            project: Project name
            service: Database service name (default: "postgres")
        
        Returns:
            Database username string
        
        Examples:
            >>> ResourceResolver.get_db_user("myapp")
            'myapp_user'
        """
        return f"{project}_user"
    
    @staticmethod
    def get_db_connection_string(project: str, env: str, service: str = "postgres") -> str:
        """
        Generate complete PostgreSQL connection string.
        
        Format: postgresql://{user}:{password}@{host}:{port}/{database}
        
        Uses generic service methods for password/host/port resolution.
        
        Args:
            project: Project name
            env: Environment name
            service: Database service name (default: "postgres")
        
        Returns:
            Complete PostgreSQL connection URL
        
        Examples:
            >>> ResourceResolver.get_db_connection_string("myapp", "prod")
            'postgresql://myapp_user:secret123@localhost:5234/myapp_8e9fb088'
        """
        user = ResourceResolver.get_db_user(project, service)
        password = ResourceResolver.get_service_password(project, env, service)
        host = ResourceResolver.get_service_host(project, env, service)
        port = ResourceResolver.get_service_port(project, env, service)
        database = ResourceResolver.get_db_name(project, env, service)
        
        return f"postgresql://{user}:{password}@{host}:{port}/{database}"
    
    @staticmethod
    def get_redis_connection_string(project: str, env: str, service: str = "redis", db: int = 0) -> str:
        """
        Generate complete Redis connection string.
        
        Format: redis://:{password}@{host}:{port}/{db}
        
        Uses generic service methods for password/host/port resolution.
        
        Args:
            project: Project name
            env: Environment name
            service: Redis service name (default: "redis")
            db: Redis database number (default: 0)
        
        Returns:
            Complete Redis connection URL
        
        Examples:
            >>> ResourceResolver.get_redis_connection_string("myapp", "prod")
            'redis://:redispass@localhost:6891/0'
        """
        password = ResourceResolver.get_service_password(project, env, service)
        host = ResourceResolver.get_service_host(project, env, service)
        port = ResourceResolver.get_service_port(project, env, service)
        
        return f"redis://:{password}@{host}:{port}/{db}"
    
    # ========================================
    # PATH RESOLUTION
    # ========================================
    
    @staticmethod
    def get_volume_host_path(project: str, env: str, service: str,
                            path_type: Literal["config", "secrets", "files", "data", "logs", "backups", "monitoring"],
                            server_ip: str) -> str:
        """
        Get host path for volume mounting.
        
        Delegates to PathResolver which handles OS detection and path formatting.
        
        Args:
            project: Project name
            env: Environment name
            service: Service name
            path_type: Type of volume (config, secrets, data, etc.)
            server_ip: Target server IP
        
        Returns:
            Host path string (OS-appropriate format)
        
        Examples:
            >>> ResourceResolver.get_volume_host_path("myapp", "prod", "api", "config", "localhost")
            'C:/local/myapp/prod/config/api'  # On Windows
            '/local/myapp/prod/config/api'    # On Linux
        """
        return PathResolver.get_volume_host_path(project, env, service, path_type, server_ip)
    
    @staticmethod
    def get_volume_container_path(service: str,
                                  path_type: Literal["config", "secrets", "files", "data", "logs", "backups", "monitoring"]) -> str:
        """
        Get container path for volume mounting.
        
        Container paths are standardized regardless of host OS.
        Delegates to PathResolver for service-specific path mapping.
        
        Args:
            service: Service name
            path_type: Type of volume
        
        Returns:
            Container path string (always Linux-style)
        
        Examples:
            >>> ResourceResolver.get_volume_container_path("api", "config")
            '/app/config'
            
            >>> ResourceResolver.get_volume_container_path("postgres", "data")
            '/var/lib/postgresql/data'
        """
        return PathResolver.get_volume_container_path(service, path_type)
    
    @staticmethod
    def get_docker_volume_name(project: str, env: str,
                              path_type: Literal["data", "logs", "backups", "monitoring"],
                              service: Optional[str] = None) -> str:
        """
        Get Docker volume name for named volumes.
        
        Only data/logs/backups/monitoring use Docker volumes.
        Config/secrets/files use bind mounts.
        
        Args:
            project: Project name
            env: Environment name
            path_type: Type of volume (must be data/logs/backups/monitoring)
            service: Optional service name for service-specific volumes
        
        Returns:
            Docker volume name
        
        Examples:
            >>> ResourceResolver.get_docker_volume_name("myapp", "prod", "data", "postgres")
            'myapp_prod_data_postgres'
            
            >>> ResourceResolver.get_docker_volume_name("myapp", "prod", "logs")
            'myapp_prod_logs'
        """
        return PathResolver.get_docker_volume_name(project, env, path_type, service)
    
    @staticmethod
    def generate_all_volume_mounts(
        project: str,
        env: str,
        service: str,
        server_ip: str,
        use_docker_volumes: bool = True,
        user: str = "root",
        auto_create_dirs: bool = True
    ) -> list:
        """
        Generate all volume mounts for a service (facade for PathResolver).
        
        Automatically ensures host directories and Docker volumes exist before
        returning volume mounts.
        
        Args:
            project: Project name
            env: Environment name
            service: Service name
            server_ip: Target server IP (REQUIRED)
            use_docker_volumes: Use Docker volumes for data/logs (default: True)
            user: SSH user for remote servers (default: "root")
            auto_create_dirs: Auto-create directories (default: True)
        
        Returns:
            List of volume mount strings ready for docker run
        
        Examples:
            >>> ResourceResolver.generate_all_volume_mounts("myapp", "prod", "api", "10.0.0.5")
            ['C:/local/myapp/prod/config/api:/app/config:ro', ...]
        """
        return PathResolver.generate_all_volume_mounts(
            project, env, service, server_ip,
            use_docker_volumes, user, auto_create_dirs
        )

    # ========================================
    # PORT RESOLUTION
    # ========================================
    
    @staticmethod
    def get_host_port(project: str, env: str, service: str,
                     container_port: str, base_port: int = 8000) -> int:
        """
        Get host port for service container.
        
        This is the external-facing port on the host machine that maps to
        the container's internal port. Used for toggle deployments.
        
        Args:
            project: Project name
            env: Environment name
            service: Service name
            container_port: Container's internal port
            base_port: Base port for generation (default: 8000)
        
        Returns:
            Host port number (deterministic hash-based)
        
        Examples:
            >>> ResourceResolver.get_host_port("myapp", "prod", "api", "8000")
            8357
        """
        return DeploymentPortResolver.generate_host_port(
            project, env, service, container_port, base_port
        )
    
    @staticmethod
    def get_internal_port(project: str, env: str, service: str) -> int:
        """
        Get internal service discovery port.
        
        This is the stable localhost port used for service-to-service
        communication via nginx proxy. Never changes across deployments.
        
        Args:
            project: Project name
            env: Environment name
            service: Service name
        
        Returns:
            Internal port number (5000-65535 range)
        
        Examples:
            >>> ResourceResolver.get_internal_port("myapp", "prod", "api")
            5678
        """
        return DeploymentPortResolver.get_internal_port(project, env, service)
    
    @staticmethod
    def get_container_ports(service: str, dockerfile_path: Optional[str] = None) -> list:
        """
        Auto-detect container ports from Dockerfile or service conventions.
        
        Args:
            service: Service name
            dockerfile_path: Optional path to Dockerfile for port detection
        
        Returns:
            List of port strings
        
        Examples:
            >>> ResourceResolver.get_container_ports("api", "Dockerfile.myapp-prod-api")
            ['8000']
            
            >>> ResourceResolver.get_container_ports("postgres")
            ['5432']
        """
        return DeploymentPortResolver.get_container_ports(service, dockerfile_path)