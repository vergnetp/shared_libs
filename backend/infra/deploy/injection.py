"""
Auto-injection of stateful service URLs.

This module handles automatic injection of connection URLs (REDIS_URL, DATABASE_URL, etc.)
when deploying services. It provides:

1. Building env vars from discovered stateful services
2. Finding services that need redeploy after a new stateful service is added
3. Abstract interface for service discovery (implemented by deploy_api or CLI)

Usage:
    # When deploying an app, get URLs for stateful services in same project
    discovered = await discovery.get_stateful_services(project_id, env)
    env_vars = build_injection_env_vars(user, project, env, discovered)
    
    # After deploying a stateful service, find apps that need the new URL
    apps_needing_redeploy = await discovery.get_services_needing_injection(
        project_id, env, new_service_type="redis"
    )

Env Var Naming:
    Services are named based on their service_name:
    - redis → REDIS_URL
    - redis-business → REDIS_BUSINESS_URL
    - postgres → DATABASE_URL
    - postgres-analytics → DATABASE_ANALYTICS_URL
    
    This allows multiple instances of the same service type in one project.
"""

from typing import Dict, List, Optional, Protocol, Any
from dataclasses import dataclass
import logging
import re

from .env_builder import get_connection_info

logger = logging.getLogger(__name__)


# =============================================================================
# Data Types
# =============================================================================

@dataclass
class DiscoveredService:
    """A stateful service discovered in a project."""
    service_type: str  # redis, postgres, mysql, mongo
    host: str
    port: int
    service_name: str = ""  # Used for env var naming (redis-business → REDIS_BUSINESS_URL)
    service_id: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "service_type": self.service_type,
            "host": self.host,
            "port": self.port,
            "service_name": self.service_name,
            "service_id": self.service_id,
        }


@dataclass  
class ServiceNeedingRedeploy:
    """A service that needs redeploy to get new env vars."""
    service_id: str
    service_name: str
    reason: str  # e.g., "Missing REDIS_URL"
    

# =============================================================================
# Discovery Interface (implemented by deploy_api or CLI)
# =============================================================================

class ServiceDiscovery(Protocol):
    """
    Interface for discovering services in a project.
    
    Implementations:
    - deploy_api: Queries SQLite via ServiceDropletStore
    - CLI: Could query API or local state file
    """
    
    async def get_stateful_services(
        self,
        project_id: str,
        env: str,
    ) -> List[DiscoveredService]:
        """Get all stateful services deployed in project/env."""
        ...
    
    async def get_non_stateful_services(
        self,
        project_id: str,
        env: str,
    ) -> List[Dict[str, Any]]:
        """Get all non-stateful services in project/env."""
        ...


# =============================================================================
# Injection Logic
# =============================================================================

def get_env_var_prefix(service_type: str, service_name: str) -> str:
    """
    Get the env var prefix based on service name.
    
    Examples:
        redis, redis → REDIS
        redis, redis-business → REDIS_BUSINESS
        postgres, postgres → DATABASE (special case for backward compat)
        postgres, postgres-analytics → DATABASE_ANALYTICS
        mysql, mysql → MYSQL
        mysql, mysql-logs → MYSQL_LOGS
    """
    # Normalize
    svc_type = service_type.lower()
    svc_name = service_name.lower() if service_name else svc_type
    
    # Base mapping
    base_mapping = {
        "redis": "REDIS",
        "postgres": "DATABASE",  # Backward compat
        "postgresql": "DATABASE",
        "mysql": "MYSQL",
        "mariadb": "MYSQL",
        "mongo": "MONGO",
        "mongodb": "MONGO",
        "opensearch": "OPENSEARCH",
        "elasticsearch": "ELASTICSEARCH",
    }
    
    base = base_mapping.get(svc_type, svc_type.upper())
    
    # If service_name == service_type (or close), use just the base
    # e.g., redis/redis → REDIS, postgres/postgres → DATABASE
    if svc_name == svc_type or svc_name.replace("-", "") == svc_type:
        return base
    
    # Otherwise extract suffix: redis-business → BUSINESS
    # Remove the service_type prefix if present
    suffix = svc_name
    for prefix in [svc_type + "-", svc_type + "_"]:
        if suffix.startswith(prefix):
            suffix = suffix[len(prefix):]
            break
    
    # Clean up and uppercase
    suffix = re.sub(r'[^a-zA-Z0-9]', '_', suffix).upper().strip('_')
    
    if suffix:
        return f"{base}_{suffix}"
    return base


def build_injection_env_vars(
    user: str,
    project: str,
    env: str,
    discovered_services: List[DiscoveredService],
) -> Dict[str, str]:
    """
    Build env vars for all discovered stateful services.
    
    Args:
        user: User/workspace ID
        project: Project name
        env: Environment (prod, staging, dev)
        discovered_services: List of stateful services to inject URLs for
        
    Returns:
        Dict of env vars. Names are based on service_name:
        
        redis (named "redis"):
            REDIS_URL, REDIS_HOST, REDIS_PORT, REDIS_PASSWORD
            
        redis (named "redis-business"):
            REDIS_BUSINESS_URL, REDIS_BUSINESS_HOST, REDIS_BUSINESS_PORT, REDIS_BUSINESS_PASSWORD
            
        postgres (named "postgres"):
            DATABASE_URL, DATABASE_HOST, DATABASE_PORT, DATABASE_USER, DATABASE_PASSWORD
            
        postgres (named "postgres-analytics"):
            DATABASE_ANALYTICS_URL, DATABASE_ANALYTICS_HOST, etc.
    """
    env_vars = {}
    
    for svc in discovered_services:
        if not svc.host or not svc.port:
            continue
        
        # Get connection info with deterministic credentials
        info = get_connection_info(
            user=user,
            project=project,
            env=env,
            service=svc.service_type,
            host=svc.host,
            port=svc.port,
        )
        
        # Get env var prefix based on service name
        prefix = get_env_var_prefix(svc.service_type, svc.service_name)
        
        # Add connection URL
        connection_url = info.get("connection_url")
        if connection_url:
            env_vars[f"{prefix}_URL"] = connection_url
        
        # Add individual components for flexibility
        env_vars[f"{prefix}_HOST"] = svc.host
        env_vars[f"{prefix}_PORT"] = str(svc.port)
        if info.get("password"):
            env_vars[f"{prefix}_PASSWORD"] = info["password"]
        if info.get("user"):
            env_vars[f"{prefix}_USER"] = info["user"]
        if info.get("database"):
            env_vars[f"{prefix}_DB"] = info["database"]
    
    return env_vars


def get_env_var_name_for_service(service_type: str, service_name: str = None) -> str:
    """
    Get the primary env var name for a service.
    
    Args:
        service_type: Type like redis, postgres
        service_name: Optional name like redis-business
    """
    prefix = get_env_var_prefix(service_type, service_name or service_type)
    return f"{prefix}_URL"


async def find_services_needing_redeploy(
    discovery: ServiceDiscovery,
    project_id: str,
    env: str,
    new_service_type: str,
    new_service_name: str = None,
) -> List[ServiceNeedingRedeploy]:
    """
    Find non-stateful services that need redeploy after a new stateful service is added.
    
    Called after deploying redis/postgres/etc. to identify apps that don't have
    the new URL injected yet.
    
    Args:
        discovery: Service discovery implementation
        project_id: Project ID
        env: Environment
        new_service_type: Type of newly deployed service (redis, postgres, etc.)
        new_service_name: Name of the service (e.g., redis-business)
        
    Returns:
        List of services that should be redeployed
    """
    env_var_name = get_env_var_name_for_service(new_service_type, new_service_name)
    
    # Get all non-stateful services in project/env
    try:
        services = await discovery.get_non_stateful_services(project_id, env)
    except Exception as e:
        logger.warning(f"Failed to query services: {e}")
        return []
    
    needing_redeploy = []
    for svc in services:
        # Check if service was deployed without this env var
        # (We can't know for sure without checking the container, but we can
        # assume any service deployed before the stateful service needs it)
        needing_redeploy.append(ServiceNeedingRedeploy(
            service_id=svc.get("id", ""),
            service_name=svc.get("name", "unknown"),
            reason=f"Missing {env_var_name}",
        ))
    
    return needing_redeploy


def format_redeploy_warning(services: List[ServiceNeedingRedeploy]) -> str:
    """Format a warning message about services needing redeploy."""
    if not services:
        return ""
    
    names = [s.service_name for s in services]
    if len(names) == 1:
        return f"⚠️ Service '{names[0]}' should be redeployed to get the new connection URL"
    else:
        return f"⚠️ Services that should be redeployed: {', '.join(names)}"


# =============================================================================
# Injection Context (for use in deploy orchestration)
# =============================================================================

@dataclass
class InjectionContext:
    """
    Context for auto-injection during deployment.
    
    Created by the API layer with discovered services, passed to deploy orchestration.
    """
    user: str
    project: str
    env: str
    discovered_services: List[DiscoveredService]
    _built_env_vars: Optional[Dict[str, str]] = None
    
    @property
    def env_vars(self) -> Dict[str, str]:
        """Build env vars lazily."""
        if self._built_env_vars is None:
            self._built_env_vars = build_injection_env_vars(
                user=self.user,
                project=self.project,
                env=self.env,
                discovered_services=self.discovered_services,
            )
        return self._built_env_vars
    
    @classmethod
    def empty(cls, user: str, project: str, env: str) -> "InjectionContext":
        """Create empty context (no discovered services)."""
        return cls(
            user=user,
            project=project,
            env=env,
            discovered_services=[],
        )
    
    def merge_with_user_env(self, user_env: Dict[str, str]) -> Dict[str, str]:
        """
        Merge auto-injected env vars with user-provided ones.
        User-provided takes precedence.
        """
        return {**self.env_vars, **user_env}
