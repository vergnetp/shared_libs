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
"""

from typing import Dict, List, Optional, Protocol, Any
from dataclasses import dataclass
import logging

from .env_builder import get_connection_info

logger = logging.getLogger(__name__)


# =============================================================================
# Data Types
# =============================================================================

@dataclass
class DiscoveredService:
    """A stateful service discovered in a project/env."""
    service_type: str  # redis, postgres, mysql, mongo
    host: str
    port: int
    service_name: str = ""
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
        Dict of env vars like:
        {
            "REDIS_URL": "redis://:xxx@10.0.0.5:8453/0",
            "REDIS_HOST": "10.0.0.5",
            "REDIS_PORT": "8453",
            "REDIS_PASSWORD": "xxx",
            "DATABASE_URL": "postgresql://user:xxx@10.0.0.5:5432/db",
            ...
        }
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
        
        # Add connection URL with standard env var name
        env_var_name = info.get("env_var_name")
        connection_url = info.get("connection_url")
        
        if env_var_name and connection_url:
            env_vars[env_var_name] = connection_url
            
            # Also add individual components for flexibility
            service_upper = svc.service_type.upper()
            env_vars[f"{service_upper}_HOST"] = svc.host
            env_vars[f"{service_upper}_PORT"] = str(svc.port)
            if info.get("password"):
                env_vars[f"{service_upper}_PASSWORD"] = info["password"]
            if info.get("user"):
                env_vars[f"{service_upper}_USER"] = info["user"]
            if info.get("database"):
                env_vars[f"{service_upper}_DB"] = info["database"]
    
    return env_vars


def get_env_var_name_for_service(service_type: str) -> str:
    """Get the primary env var name for a service type."""
    service_lower = service_type.lower()
    if "redis" in service_lower:
        return "REDIS_URL"
    elif "postgres" in service_lower:
        return "DATABASE_URL"
    elif "mysql" in service_lower or "mariadb" in service_lower:
        return "MYSQL_URL"
    elif "mongo" in service_lower:
        return "MONGO_URL"
    return f"{service_type.upper()}_URL"


async def find_services_needing_redeploy(
    discovery: ServiceDiscovery,
    project_id: str,
    env: str,
    new_service_type: str,
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
        
    Returns:
        List of services that should be redeployed
    """
    env_var_name = get_env_var_name_for_service(new_service_type)
    
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
