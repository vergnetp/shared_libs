"""
Deploy Environment Builder - Automatic env var and volume injection.

Injects connection strings and service discovery env vars at deploy time.
This allows services to connect to databases, caches, etc. without
importing any infra code.

Standard env vars injected:
    DEPLOY_USER       - User ID
    DEPLOY_PROJECT    - Project name
    DEPLOY_ENV        - Environment (prod, staging, dev)
    DEPLOY_SERVICE    - Service name
    
    DATABASE_URL      - PostgreSQL connection string (if postgres in project)
    DB_HOST           - Database host (localhost via nginx)
    DB_PORT           - Database internal port
    DB_NAME           - Database name
    DB_USER           - Database user
    
    REDIS_URL         - Redis connection string (if redis in project)
    REDIS_HOST        - Redis host
    REDIS_PORT        - Redis port
"""

import hashlib
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field

from ..networking.ports import DeploymentPortResolver
from ..storage.volumes import VolumeManager, VolumeMount


@dataclass
class ServiceDependency:
    """Detected service dependency."""
    name: str           # postgres, redis, etc.
    env_prefix: str     # DB_, REDIS_, etc.
    container_port: int # 5432, 6379, etc.


# Known service types and their env var prefixes
KNOWN_SERVICES = {
    "postgres": ServiceDependency("postgres", "DB", 5432),
    "postgresql": ServiceDependency("postgres", "DB", 5432),
    "mysql": ServiceDependency("mysql", "DB", 3306),
    "mariadb": ServiceDependency("mariadb", "DB", 3306),
    "redis": ServiceDependency("redis", "REDIS", 6379),
    "mongo": ServiceDependency("mongo", "MONGO", 27017),
    "mongodb": ServiceDependency("mongodb", "MONGO", 27017),
    "opensearch": ServiceDependency("opensearch", "SEARCH", 9200),
    "elasticsearch": ServiceDependency("elasticsearch", "SEARCH", 9200),
}


class DeployEnvBuilder:
    """
    Build environment variables and volumes for deployment.
    
    Automatically injects:
    - Service discovery env vars (DB_HOST, DB_PORT, etc.)
    - Connection strings (DATABASE_URL, REDIS_URL)
    - Standard deploy context (DEPLOY_USER, DEPLOY_PROJECT, etc.)
    - Volume mounts for persistence
    
    Usage:
        builder = DeployEnvBuilder(
            user="u1",
            project="myapp",
            env="prod",
            service="api",
        )
        
        # Add dependencies (services this service needs to connect to)
        builder.add_dependency("postgres")
        builder.add_dependency("redis")
        
        # Get env vars to inject
        env_vars = builder.build_env_vars()
        # -> {
        #     "DEPLOY_USER": "u1",
        #     "DEPLOY_PROJECT": "myapp",
        #     "DEPLOY_ENV": "prod",
        #     "DB_HOST": "localhost",
        #     "DB_PORT": "5186",
        #     "DATABASE_URL": "postgresql://u1_myapp_prod_postgres_user@localhost:5186/u1_myapp_abc123",
        #     "REDIS_HOST": "localhost",
        #     "REDIS_PORT": "5234",
        #     "REDIS_URL": "redis://localhost:5234/0",
        # }
        
        # Get volumes if service needs persistence
        volumes = builder.get_volumes()
    """
    
    def __init__(
        self,
        user: str,
        project: str,
        env: str,
        service: str,
        base_env_vars: Dict[str, str] = None,
    ):
        self.user = user
        self.project = project
        self.env = env
        self.service = service
        self.base_env_vars = base_env_vars or {}
        self.dependencies: Set[str] = set()
        self.volume_manager = VolumeManager()
        self._include_volumes = True
        self._password_mode = "env"  # "env" or "file"
    
    def add_dependency(self, service_name: str) -> 'DeployEnvBuilder':
        """
        Add a service dependency.
        
        Args:
            service_name: Name of service this service depends on (postgres, redis, etc.)
            
        Returns:
            Self for chaining
        """
        self.dependencies.add(service_name.lower())
        return self
    
    def add_dependencies(self, services: List[str]) -> 'DeployEnvBuilder':
        """Add multiple dependencies."""
        for s in services:
            self.add_dependency(s)
        return self
    
    def detect_dependencies_from_project(self, project_services: List[str]) -> 'DeployEnvBuilder':
        """
        Auto-detect dependencies from project's service list.
        
        If project has postgres, redis, etc. - assume this service needs them.
        
        Args:
            project_services: List of all service names in the project
            
        Returns:
            Self for chaining
        """
        for svc in project_services:
            svc_lower = svc.lower()
            # Don't add self as dependency
            if svc_lower == self.service.lower():
                continue
            # Add known stateful services as dependencies
            if svc_lower in KNOWN_SERVICES:
                self.add_dependency(svc_lower)
        return self
    
    def with_volumes(self, include: bool = True) -> 'DeployEnvBuilder':
        """Enable/disable volume mounts."""
        self._include_volumes = include
        return self
    
    def with_password_mode(self, mode: str) -> 'DeployEnvBuilder':
        """Set password mode: 'env' (in env vars) or 'file' (mounted)."""
        self._password_mode = mode
        return self
    
    def _get_internal_port(self, service: str) -> int:
        """Get internal port for a service."""
        return DeploymentPortResolver.get_internal_port(
            self.user, self.project, self.env, service
        )
    
    def _get_db_name(self, service: str) -> str:
        """Generate database name."""
        hash_input = f"{self.project}_{self.env}_{service}"
        suffix = hashlib.md5(hash_input.encode()).hexdigest()[:8]
        return f"{self.user}_{self.project}_{suffix}"
    
    def _get_db_user(self, service: str) -> str:
        """Generate database user."""
        return f"{self.user}_{self.project}_{self.env}_{service}_user"
    
    def _get_service_password(self, service: str) -> str:
        """
        Generate deterministic password for a service.
        
        Uses hash of user/project/env/service to create reproducible password.
        Same inputs = same password (no need to store).
        """
        hash_input = f"{self.user}_{self.project}_{self.env}_{service}_password_v1"
        return hashlib.sha256(hash_input.encode()).hexdigest()[:32]
    
    def _build_postgres_env(self) -> Dict[str, str]:
        """Build PostgreSQL env vars."""
        port = self._get_internal_port("postgres")
        db_name = self._get_db_name("postgres")
        db_user = self._get_db_user("postgres")
        db_password = self._get_service_password("postgres")
        
        env = {
            "DB_HOST": "localhost",
            "DB_PORT": str(port),
            "DB_NAME": db_name,
            "DB_USER": db_user,
            "DB_PASSWORD": db_password,
            "DATABASE_URL": f"postgresql://{db_user}:{db_password}@localhost:{port}/{db_name}",
        }
        
        # If file mode, app reads from /app/secrets/postgres_password
        if self._password_mode == "file":
            env["DB_PASSWORD_FILE"] = "/app/secrets/postgres_password"
            # Still include password for apps that don't support file mode
        
        return env
    
    def _build_redis_env(self) -> Dict[str, str]:
        """Build Redis env vars."""
        port = self._get_internal_port("redis")
        password = self._get_service_password("redis")
        
        return {
            "REDIS_HOST": "localhost",
            "REDIS_PORT": str(port),
            "REDIS_PASSWORD": password,
            "REDIS_URL": f"redis://:{password}@localhost:{port}/0",
        }
    
    def _build_mongo_env(self) -> Dict[str, str]:
        """Build MongoDB env vars."""
        port = self._get_internal_port("mongo")
        db_name = self._get_db_name("mongo")
        db_user = self._get_db_user("mongo")
        db_password = self._get_service_password("mongo")
        
        return {
            "MONGO_HOST": "localhost",
            "MONGO_PORT": str(port),
            "MONGO_DB": db_name,
            "MONGO_USER": db_user,
            "MONGO_PASSWORD": db_password,
            "MONGO_URL": f"mongodb://{db_user}:{db_password}@localhost:{port}/{db_name}",
        }
    
    def _build_search_env(self, service: str) -> Dict[str, str]:
        """Build OpenSearch/Elasticsearch env vars."""
        port = self._get_internal_port(service)
        
        return {
            "SEARCH_HOST": "localhost",
            "SEARCH_PORT": str(port),
            "SEARCH_URL": f"http://localhost:{port}",
            "OPENSEARCH_URL": f"http://localhost:{port}",
            "ELASTICSEARCH_URL": f"http://localhost:{port}",
        }
    
    def build_env_vars(self) -> Dict[str, str]:
        """
        Build complete env vars dict.
        
        Returns:
            Dict of environment variables to inject
        """
        env = {}
        
        # Standard deploy context
        env["DEPLOY_USER"] = self.user
        env["DEPLOY_PROJECT"] = self.project
        env["DEPLOY_ENV"] = self.env
        env["DEPLOY_SERVICE"] = self.service
        
        # Service-specific env vars
        for dep in self.dependencies:
            if dep in ("postgres", "postgresql"):
                env.update(self._build_postgres_env())
            elif dep in ("redis",):
                env.update(self._build_redis_env())
            elif dep in ("mongo", "mongodb"):
                env.update(self._build_mongo_env())
            elif dep in ("opensearch", "elasticsearch"):
                env.update(self._build_search_env(dep))
        
        # Base env vars (user-provided) override auto-generated
        env.update(self.base_env_vars)
        
        return env
    
    def get_volumes(self) -> List[VolumeMount]:
        """
        Get volume mounts for this service.
        
        Returns:
            List of VolumeMount objects
        """
        if not self._include_volumes:
            return []
        
        return self.volume_manager.get_standard_service_volumes(
            self.user, self.project, self.env, self.service
        )
    
    def get_volumes_docker(self) -> List[str]:
        """Get volumes in Docker -v format."""
        return [v.to_docker() for v in self.get_volumes()]
    
    def get_secrets_volumes(self) -> List[VolumeMount]:
        """
        Get secrets volume mounts.
        
        Mounts secrets from all dependencies into /app/secrets.
        """
        volumes = []
        for dep in self.dependencies:
            if dep in KNOWN_SERVICES:
                mount = self.volume_manager.get_volume_mount(
                    self.user, self.project, self.env, dep, "secrets", readonly=True
                )
                # Override container path to /app/secrets for consumer services
                mount.container_path = "/app/secrets"
                volumes.append(mount)
        return volumes
    
    def build_stateful_service_env(self) -> Dict[str, str]:
        """
        Build env vars for stateful services (postgres, redis, etc.).
        
        These are the env vars needed by the SERVICE ITSELF, not consumers.
        For example, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB for postgres container.
        
        Returns:
            Dict of environment variables
        """
        service_lower = self.service.lower()
        env = {}
        
        # Standard deploy context
        env["DEPLOY_USER"] = self.user
        env["DEPLOY_PROJECT"] = self.project
        env["DEPLOY_ENV"] = self.env
        env["DEPLOY_SERVICE"] = self.service
        
        if service_lower in ("postgres", "postgresql"):
            db_name = self._get_db_name("postgres")
            db_user = self._get_db_user("postgres")
            db_password = self._get_service_password("postgres")
            
            env["POSTGRES_DB"] = db_name
            env["POSTGRES_USER"] = db_user
            env["POSTGRES_PASSWORD"] = db_password
            
        elif service_lower in ("mysql", "mariadb"):
            db_name = self._get_db_name(service_lower)
            db_user = self._get_db_user(service_lower)
            db_password = self._get_service_password(service_lower)
            root_password = self._get_service_password(f"{service_lower}_root")
            
            env["MYSQL_DATABASE"] = db_name
            env["MYSQL_USER"] = db_user
            env["MYSQL_PASSWORD"] = db_password
            env["MYSQL_ROOT_PASSWORD"] = root_password
            
        elif service_lower == "redis":
            password = self._get_service_password("redis")
            # Redis 6+ uses ACL, but most use requirepass
            env["REDIS_PASSWORD"] = password
            # For redis:alpine with redis-server --requirepass
            
        elif service_lower in ("mongo", "mongodb"):
            db_user = self._get_db_user("mongo")
            db_password = self._get_service_password("mongo")
            
            env["MONGO_INITDB_ROOT_USERNAME"] = db_user
            env["MONGO_INITDB_ROOT_PASSWORD"] = db_password
        
        # Base env vars (user-provided) override auto-generated
        env.update(self.base_env_vars)
        
        return env


def build_deploy_env(
    user: str,
    project: str,
    env: str,
    service: str,
    project_services: List[str] = None,
    base_env_vars: Dict[str, str] = None,
) -> Dict[str, str]:
    """
    Convenience function to build env vars.
    
    Args:
        user: User ID
        project: Project name
        env: Environment
        service: Service being deployed
        project_services: All services in project (for auto-detection)
        base_env_vars: User-provided env vars (override auto-generated)
        
    Returns:
        Complete env vars dict
    """
    builder = DeployEnvBuilder(user, project, env, service, base_env_vars)
    
    if project_services:
        builder.detect_dependencies_from_project(project_services)
    
    return builder.build_env_vars()


def build_deploy_volumes(
    user: str,
    project: str,
    env: str,
    service: str,
) -> List[str]:
    """
    Convenience function to build volume mounts.
    
    Returns:
        List of Docker -v format volume strings
    """
    builder = DeployEnvBuilder(user, project, env, service)
    return builder.get_volumes_docker()


def build_stateful_service_env(
    user: str,
    project: str,
    env: str,
    service: str,
    base_env_vars: Dict[str, str] = None,
) -> Dict[str, str]:
    """
    Build env vars for stateful services (postgres, redis, etc.).
    
    These are the env vars needed BY the service container itself.
    For example: POSTGRES_USER, POSTGRES_PASSWORD for postgres container.
    
    Args:
        user: User ID
        project: Project name
        env: Environment
        service: Service name (postgres, redis, mysql, etc.)
        base_env_vars: User-provided env vars (override auto-generated)
        
    Returns:
        Dict of environment variables for the service container
    """
    builder = DeployEnvBuilder(user, project, env, service, base_env_vars)
    return builder.build_stateful_service_env()


def is_stateful_service(service_name: str) -> bool:
    """Check if a service is a known stateful service."""
    return service_name.lower() in KNOWN_SERVICES
