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
    is_stateful: bool = True  # True for databases/caches, False for HTTP services


@dataclass  
class StatefulServiceType:
    """Deployable stateful service type (for UI)."""
    value: str          # postgres, redis, etc. (used as service name)
    label: str          # Display label with emoji
    port: int           # Container port
    image: str          # Docker image (should match snapshot pre-pulls)
    env_prefix: str     # Env var prefix (DB_, REDIS_, etc.)
    description: str = ""  # Optional description
    
    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "label": self.label,
            "port": self.port,
            "image": self.image,
            "env_prefix": self.env_prefix,
            "description": self.description,
        }


# Deployable stateful service types (for frontend UI)
# Images should match SNAPSHOT_PRESETS["base"].docker_images in cloudinit.py
STATEFUL_SERVICE_TYPES = [
    StatefulServiceType("postgres", "🐘 PostgreSQL", 5432, "postgres:16-alpine", "DB", "Relational database"),
    StatefulServiceType("redis", "⚡ Redis", 6379, "redis:7-alpine", "REDIS", "In-memory cache & pub/sub"),
    StatefulServiceType("mysql", "🐬 MySQL", 3306, "mysql:8", "DB", "Relational database"),
    StatefulServiceType("mongo", "🍃 MongoDB", 27017, "mongo:7", "MONGO", "Document database"),
    StatefulServiceType("opensearch", "🔍 OpenSearch", 9200, "opensearchproject/opensearch:2", "SEARCH", "Search & analytics"),
    StatefulServiceType("qdrant", "🧠 Qdrant", 6333, "qdrant/qdrant:latest", "VECTOR", "Vector database for AI"),
]


def get_stateful_service_types() -> list:
    """Get list of deployable stateful service types (for frontend)."""
    return [s.to_dict() for s in STATEFUL_SERVICE_TYPES]


def get_stateful_image(service_type: str) -> Optional[str]:
    """Get Docker image for a stateful service type."""
    for s in STATEFUL_SERVICE_TYPES:
        if s.value == service_type:
            return s.image
    return None


# Known service types and their env var prefixes
KNOWN_SERVICES = {
    # Stateful services (TCP stream proxy + credentials)
    "postgres": ServiceDependency("postgres", "DB", 5432, is_stateful=True),
    "postgresql": ServiceDependency("postgres", "DB", 5432, is_stateful=True),
    "mysql": ServiceDependency("mysql", "DB", 3306, is_stateful=True),
    "mariadb": ServiceDependency("mariadb", "DB", 3306, is_stateful=True),
    "redis": ServiceDependency("redis", "REDIS", 6379, is_stateful=True),
    "mongo": ServiceDependency("mongo", "MONGO", 27017, is_stateful=True),
    "mongodb": ServiceDependency("mongodb", "MONGO", 27017, is_stateful=True),
    "opensearch": ServiceDependency("opensearch", "SEARCH", 9200, is_stateful=True),
    "elasticsearch": ServiceDependency("elasticsearch", "SEARCH", 9200, is_stateful=True),
    "rabbitmq": ServiceDependency("rabbitmq", "RABBITMQ", 5672, is_stateful=True),
    "kafka": ServiceDependency("kafka", "KAFKA", 9092, is_stateful=True),
}

# Default port for HTTP services
DEFAULT_HTTP_PORT = 8000


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
    
    def _build_http_service_env(self, service_name: str) -> Dict[str, str]:
        """
        Build env vars for a generic HTTP service.
        
        For any service not in KNOWN_SERVICES (workers, APIs, LLMs, etc).
        Generates {SERVICE_NAME}_URL pointing to localhost:{internal_port}.
        
        Users can use this internal URL (fast) or the public domain.
        """
        port = self._get_internal_port(service_name)
        service_upper = service_name.upper().replace("-", "_").replace(".", "_")
        
        return {
            f"{service_upper}_HOST": "localhost",
            f"{service_upper}_PORT": str(port),
            f"{service_upper}_URL": f"http://localhost:{port}",
        }
    
    def build_env_vars(self) -> Dict[str, str]:
        """
        Build complete env vars dict.
        
        Auto-injects connection URLs for ALL service dependencies:
        
        Stateful services (special format with credentials):
        - postgres → DATABASE_URL, DB_HOST, DB_PORT, etc.
        - redis → REDIS_URL, REDIS_HOST, REDIS_PORT
        - mongo → MONGO_URL, MONGO_HOST, MONGO_PORT
        - opensearch → OPENSEARCH_URL, SEARCH_HOST, SEARCH_PORT
        
        HTTP services (simple format):
        - llm → LLM_URL, LLM_HOST, LLM_PORT
        - worker → WORKER_URL, WORKER_HOST, WORKER_PORT
        - {any} → {SERVICE}_URL, {SERVICE}_HOST, {SERVICE}_PORT
        
        Users can use internal URL (fast) or public domain (https://service.digitalpixo.com).
        
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
            dep_lower = dep.lower()
            
            # Stateful services get special handling (with credentials)
            if dep_lower in ("postgres", "postgresql"):
                env.update(self._build_postgres_env())
            elif dep_lower in ("redis",):
                env.update(self._build_redis_env())
            elif dep_lower in ("mongo", "mongodb"):
                env.update(self._build_mongo_env())
            elif dep_lower in ("opensearch", "elasticsearch"):
                env.update(self._build_search_env(dep_lower))
            else:
                # HTTP services get simple URL injection
                env.update(self._build_http_service_env(dep_lower))
        
        # Base env vars (user-provided) override auto-generated
        env.update(self.base_env_vars)
        
        return env
        
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
    """Check if a service is a known stateful service (needs TCP proxy with credentials)."""
    service_lower = service_name.lower()
    if service_lower in KNOWN_SERVICES:
        return KNOWN_SERVICES[service_lower].is_stateful
    return False


def get_service_container_port(service_name: str) -> int:
    """Get the container port for a service."""
    service_lower = service_name.lower()
    if service_lower in KNOWN_SERVICES:
        return KNOWN_SERVICES[service_lower].container_port
    return DEFAULT_HTTP_PORT


def get_connection_info(
    user: str,
    project: str,
    env: str,
    service: str,
    host: str,
    port: int,
) -> Dict[str, str]:
    """
    Generate connection info for a stateful service.
    
    Returns deterministic credentials based on user/project/env/service.
    Can be called anytime to regenerate the same connection URL.
    
    Args:
        user: User/workspace ID
        project: Project name
        env: Environment (prod, staging, dev)
        service: Service name (redis, postgres, etc.)
        host: Server IP or hostname
        port: Port the service is exposed on
        
    Returns:
        Dict with keys: connection_url, env_var_name, host, port, password, user (if applicable), database (if applicable)
    """
    builder = DeployEnvBuilder(user, project, env, service)
    service_lower = service.lower()
    
    result = {
        "host": host,
        "port": port,
        "service": service,
    }
    
    if "redis" in service_lower:
        password = builder._get_service_password("redis")
        result.update({
            "connection_url": f"redis://:{password}@{host}:{port}/0",
            "env_var_name": "REDIS_URL",
            "password": password,
        })
    elif "postgres" in service_lower:
        db_user = builder._get_db_user("postgres")
        db_password = builder._get_service_password("postgres")
        db_name = builder._get_db_name("postgres")
        result.update({
            "connection_url": f"postgresql://{db_user}:{db_password}@{host}:{port}/{db_name}",
            "env_var_name": "DATABASE_URL",
            "user": db_user,
            "password": db_password,
            "database": db_name,
        })
    elif "mysql" in service_lower or "mariadb" in service_lower:
        db_user = builder._get_db_user("mysql")
        db_password = builder._get_service_password("mysql")
        db_name = builder._get_db_name("mysql")
        result.update({
            "connection_url": f"mysql://{db_user}:{db_password}@{host}:{port}/{db_name}",
            "env_var_name": "MYSQL_URL",
            "user": db_user,
            "password": db_password,
            "database": db_name,
        })
    elif "mongo" in service_lower:
        db_user = builder._get_db_user("mongo")
        db_password = builder._get_service_password("mongo")
        db_name = builder._get_db_name("mongo")
        result.update({
            "connection_url": f"mongodb://{db_user}:{db_password}@{host}:{port}/{db_name}",
            "env_var_name": "MONGO_URL",
            "user": db_user,
            "password": db_password,
            "database": db_name,
        })
    else:
        result.update({
            "connection_url": f"{host}:{port}",
            "env_var_name": f"{service.upper()}_URL",
        })
    
    return result


def build_discovered_service_urls(
    user: str,
    project: str,
    env: str,
    discovered_services: list,
) -> Dict[str, str]:
    """
    Build env vars for all discovered stateful services in a project.
    
    This is called when deploying any service - it auto-injects URLs for
    all stateful services (redis, postgres, etc.) that exist in the same project/env.
    
    Args:
        user: User/workspace ID
        project: Project name
        env: Environment (prod, staging, dev)
        discovered_services: List of dicts with {service_type, host, port}
            e.g., [{"service_type": "redis", "host": "10.0.0.5", "port": 8453}]
    
    Returns:
        Dict of env vars like:
        {
            "REDIS_URL": "redis://:xxx@10.0.0.5:8453/0",
            "DATABASE_URL": "postgresql://user:xxx@10.0.0.5:5432/db",
        }
    """
    env_vars = {}
    
    for svc in discovered_services:
        service_type = svc.get("service_type", "").lower()
        host = svc.get("host")
        port = svc.get("port")
        
        if not host or not port:
            continue
        
        info = get_connection_info(
            user=user,
            project=project,
            env=env,
            service=service_type,
            host=host,
            port=port,
        )
        
        # Add connection URL with standard env var name
        env_var_name = info.get("env_var_name")
        connection_url = info.get("connection_url")
        
        if env_var_name and connection_url:
            env_vars[env_var_name] = connection_url
            
            # Also add individual components for flexibility
            service_upper = service_type.upper()
            env_vars[f"{service_upper}_HOST"] = host
            env_vars[f"{service_upper}_PORT"] = str(port)
            if info.get("password"):
                env_vars[f"{service_upper}_PASSWORD"] = info["password"]
            if info.get("user"):
                env_vars[f"{service_upper}_USER"] = info["user"]
            if info.get("database"):
                env_vars[f"{service_upper}_DB"] = info["database"]
    
    return env_vars
