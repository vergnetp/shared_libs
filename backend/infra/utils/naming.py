# backend/infra/deployment_naming.py
"""
Centralized naming functions for all deployment artifacts.

CRITICAL: This is the SINGLE source of truth for all naming conventions.
If the naming pattern changes (e.g., adding org prefix), only update this file.
"""
from typing import Dict, Optional, List
import re


def sanitize_for_dns(name: str) -> str:
    """
    Sanitize a string for use in DNS-compatible names (e.g., droplet names).
    
    Rules:
    - Only alphanumeric characters and hyphens allowed
    - No leading/trailing hyphens
    - Lowercase
    - Underscores and spaces become hyphens
    
    Examples:
        sanitize_for_dns("demo_user") -> "demo-user"
        sanitize_for_dns("My Project!") -> "my-project"
    """
    if not name:
        return ""
    # Replace underscores and spaces with hyphens
    name = re.sub(r'[_\s]+', '-', name)
    # Remove anything that's not alphanumeric or hyphen
    name = re.sub(r'[^a-zA-Z0-9-]', '', name)
    # Collapse multiple hyphens
    name = re.sub(r'-+', '-', name)
    # Strip leading/trailing hyphens and lowercase
    return name.strip('-').lower()


def sanitize_for_tag(name: str) -> str:
    """
    Sanitize a string for use in DO tags.
    
    DO tag rules:
    - Letters, numbers, colons, dashes, underscores allowed
    - Max 255 characters
    - Lowercase recommended for consistency
    
    Examples:
        sanitize_for_tag("demo_user") -> "demo_user"
        sanitize_for_tag("My Project!") -> "my-project"
    """
    if not name:
        return ""
    # Replace spaces with hyphens
    name = re.sub(r'\s+', '-', name)
    # Remove anything that's not alphanumeric, colon, dash, or underscore
    name = re.sub(r'[^a-zA-Z0-9:_-]', '', name)
    # Lowercase for consistency
    return name.lower()[:255]


def sanitize_for_docker(name: str) -> str:
    """
    Sanitize a string for use in Docker names (containers, volumes, networks).
    
    Rules:
    - Alphanumeric, underscores, hyphens, periods allowed
    - Must start with alphanumeric
    - Lowercase
    
    Examples:
        sanitize_for_docker("My Container!") -> "my_container"
    """
    if not name:
        return ""
    # Replace spaces with underscores
    name = re.sub(r'\s+', '_', name)
    # Remove invalid characters
    name = re.sub(r'[^a-zA-Z0-9_.-]', '', name)
    # Ensure starts with alphanumeric
    name = re.sub(r'^[^a-zA-Z0-9]+', '', name)
    return name.lower()


def generate_friendly_name() -> str:
    """
    Generate a random friendly name like 'brave-tiger'.
    
    Useful for droplet names where human-readability matters
    more than encoding metadata in the name.
    
    Examples:
        generate_friendly_name() -> "swift-falcon"
        generate_friendly_name() -> "calm-otter"
    """
    import random
    
    adjectives = [
        "brave", "calm", "eager", "fancy", "gentle", "happy", "jolly", "kind",
        "lively", "merry", "nice", "proud", "quick", "sharp", "swift", "wise",
        "bold", "bright", "cool", "daring", "epic", "fast", "grand", "keen"
    ]
    nouns = [
        "tiger", "eagle", "wolf", "falcon", "lion", "bear", "hawk", "fox",
        "otter", "raven", "shark", "whale", "cobra", "puma", "lynx", "elk",
        "owl", "swan", "crane", "heron", "dragon", "phoenix", "panther", "jaguar"
    ]
    return f"{random.choice(adjectives)}-{random.choice(nouns)}"


class DONaming:
    """
    DigitalOcean naming - SINGLE SOURCE OF TRUTH.
    
    Use this class for both creating and retrieving droplets
    to ensure consistency.
    
    Usage:
        naming = DONaming(user_id="demo_user", project="myapp", env="prod")
        
        # When creating
        droplet_name = naming.droplet_name(service="api", index=1)
        tags = naming.tags(service="api")
        
        # When filtering/listing
        filter_tags = naming.filter_tags()  # Same format
    """
    
    def __init__(self, user_id: str, project: str, env: str):
        self.user_id = user_id
        self.project = project
        self.env = env
        
        # Pre-compute sanitized versions
        self._user_dns = sanitize_for_dns(user_id)
        self._project_dns = sanitize_for_dns(project)
        self._env_dns = sanitize_for_dns(env)
        
        self._user_tag = sanitize_for_tag(user_id)
        self._project_tag = sanitize_for_tag(project)
        self._env_tag = sanitize_for_tag(env)
    
    def droplet_name(self, service: str, index: int) -> str:
        """Generate sanitized droplet name."""
        service_dns = sanitize_for_dns(service)
        return f"{self._user_dns}-{self._project_dns}-{self._env_dns}-{service_dns}-{index}"
    
    def tags(self, service: str) -> List[str]:
        """Generate tags for a droplet."""
        service_tag = sanitize_for_tag(service)
        return [
            f"user:{self._user_tag}",
            f"project:{self._project_tag}",
            f"env:{self._env_tag}",
            f"service:{service_tag}",
        ]
    
    def filter_tags(self, service: Optional[str] = None) -> Dict[str, str]:
        """
        Get tags for filtering droplets.
        
        Returns dict that can be used to match droplet tags.
        """
        tags = {
            "user": f"user:{self._user_tag}",
            "project": f"project:{self._project_tag}",
            "env": f"env:{self._env_tag}",
        }
        if service:
            tags["service"] = f"service:{sanitize_for_tag(service)}"
        return tags
    
    def matches_tags(self, droplet_tags: List[str], service: Optional[str] = None) -> bool:
        """Check if droplet tags match our filter."""
        filter_tags = self.filter_tags(service)
        required = [filter_tags["user"], filter_tags["project"], filter_tags["env"]]
        if service:
            required.append(filter_tags["service"])
        return all(tag in droplet_tags for tag in required)


class DeploymentNaming:
    """
    Centralized naming functions for all deployment artifacts.
    
    CRITICAL: This is the SINGLE source of truth for all naming conventions.
    
    Container name format: {workspace_id[:6]}_{project}_{env}_{service}
    
    The workspace_id is shortened to 6 characters for readability while
    maintaining uniqueness (6 hex chars = 16M combinations).
    
    Examples:
        - 7f3a2b_hostomatic_prod_api
        - 7f3a2b_hostomatic_prod_worker
        - 7f3a2b_mediator_dev_api
    """

    @staticmethod
    def get_workspace_short(workspace_id: str) -> str:
        """
        Get shortened workspace ID for container naming.
        
        Uses first 6 characters of workspace_id.
        Handles UUIDs (with hyphens) by removing hyphens first.
        
        Examples:
            get_workspace_short("7f3a2b9c-4d5e-6f7a-8b9c-0d1e2f3a4b5c") -> "7f3a2b"
            get_workspace_short("abc123") -> "abc123"
        """
        # Remove hyphens for UUID format, take first 6 chars
        clean_id = workspace_id.replace("-", "")
        return clean_id[:6].lower()

    @staticmethod
    def get_service_name(service_name: str) -> str:
        """Return the service name unchanged.

        This method exists for symmetry with other helpers and future extension.
        """
        return service_name

    @staticmethod
    def get_container_name(workspace_id: str, project: str, env: str, service_name: str, secondary: bool = False) -> str:
        """Generate Docker container name using the standard convention.

        Format: ``<ws_short>_<project>_<env>_<service>`` (primary)
        Format: ``<ws_short>_<project>_<env>_<service>_secondary`` (secondary)
        
        This is the SERVICE IDENTITY and LOCK KEY.
        
        For zero-downtime deployments, we use toggle between primary and secondary.

        Examples:
        - get_container_name("7f3a2b...", "hostomatic", "prod", "api") -> "7f3a2b_hostomatic_prod_api"
        - get_container_name("7f3a2b...", "hostomatic", "prod", "api", secondary=True) -> "7f3a2b_hostomatic_prod_api_secondary"
        """
        ws_short = DeploymentNaming.get_workspace_short(workspace_id)
        effective_service = DeploymentNaming.get_service_name(service_name)
        base_name = f"{ws_short}_{project}_{env}_{effective_service}"
        return f"{base_name}_secondary" if secondary else base_name

    @staticmethod
    def get_secondary_container_name(container_name: str) -> str:
        """Get the secondary version of a container name.
        
        If already secondary, returns primary.
        If primary, returns secondary.
        
        Examples:
            "myapp_prod_api" -> "myapp_prod_api_secondary"
            "myapp_prod_api_secondary" -> "myapp_prod_api"
        """
        if container_name.endswith("_secondary"):
            return container_name[:-10]  # Remove "_secondary"
        return f"{container_name}_secondary"
    
    @staticmethod
    def is_secondary_container(container_name: str) -> bool:
        """Check if a container name is a secondary (toggle) container."""
        return container_name.endswith("_secondary")
    
    @staticmethod
    def get_base_container_name(container_name: str) -> str:
        """Get the base container name, stripping any toggle suffix.
        
        Handles both _primary and _secondary suffixes.
        
        Examples:
            "myapp_prod_api" -> "myapp_prod_api"
            "myapp_prod_api_primary" -> "myapp_prod_api"
            "myapp_prod_api_secondary" -> "myapp_prod_api"
        """
        if container_name.endswith("_secondary"):
            return container_name[:-10]
        if container_name.endswith("_primary"):
            return container_name[:-8]
        return container_name
    
    @staticmethod
    def get_local_image_name(container_name: str, version: int = None, deployment_id: str = None) -> str:
        """Generate local Docker image name for a deployment.
        
        Uses version tag if available (successful deploys), otherwise deployment_id tag.
        
        Args:
            container_name: Container name (may include _primary/_secondary suffix)
            version: Version number (for successful deployments)
            deployment_id: Deployment ID (fallback for failed/pending deployments)
            
        Returns:
            Image name like "abc12_proj_prod_svc:v5" or "abc12_proj_prod_svc:deploy_xyz"
            
        Examples:
            get_local_image_name("abc_proj_prod_api_primary", version=5) -> "abc_proj_prod_api:v5"
            get_local_image_name("abc_proj_prod_api", deployment_id="xyz123") -> "abc_proj_prod_api:deploy_xyz123"
        """
        base_name = DeploymentNaming.get_base_container_name(container_name)
        
        if version is not None:
            return f"{base_name}:v{version}"
        elif deployment_id:
            clean_id = deployment_id.replace("deploy_", "")
            return f"{base_name}:deploy_{clean_id}"
        else:
            return f"{base_name}:latest"
    
    @staticmethod
    def parse_image_version(image_name: str) -> Optional[int]:
        """Extract version number from image tag.
        
        Args:
            image_name: Image name like "abc_proj_prod_api:v5"
            
        Returns:
            Version number (5) or None if not a version tag
            
        Examples:
            parse_image_version("abc_proj_prod_api:v5") -> 5
            parse_image_version("abc_proj_prod_api:deploy_xyz") -> None
            parse_image_version("abc_proj_prod_api:latest") -> None
        """
        if ":" not in image_name:
            return None
        tag = image_name.split(":")[-1]
        if tag.startswith("v") and tag[1:].isdigit():
            return int(tag[1:])
        return None

    @staticmethod
    def get_container_name_pattern(workspace_id: str, project: str, env: str, service_name: str) -> str:
        """
        Generate wildcard pattern for finding service containers (both primary and secondary).
        
        Used to find all containers for a service regardless of toggle state.
        
        Format: ``<ws_short>_<project>_<env>_<service>*``
        
        Matches:
        - 7f3a2b_myproj_dev_api (primary)
        - 7f3a2b_myproj_dev_api_secondary (secondary)
        """
        base_name = DeploymentNaming.get_container_name(workspace_id, project, env, service_name)
        return f"{base_name}*"

    @staticmethod
    def parse_container_name(container_name: str) -> Optional[Dict[str, str]]:
        """
        Parse container name to extract components.
        
        CRITICAL: This is the INVERSE of get_container_name().
        If you change get_container_name() format, update this method too!
        
        Current format: {ws_short}_{project}_{env}_{service}
        Service may contain underscores (e.g., "cleanup_job").
        
        Args:
            container_name: Container name (e.g., "7f3a2b_myapp_prod_api")
            
        Returns:
            Dict with keys: workspace_short, project, env, service
            Or None if parsing fails
            
        Examples:
            >>> DeploymentNaming.parse_container_name("7f3a2b_myapp_prod_api")
            {"workspace_short": "7f3a2b", "project": "myapp", "env": "prod", "service": "api"}
            
            >>> DeploymentNaming.parse_container_name("7f3a2b_myapp_prod_cleanup_job")
            {"workspace_short": "7f3a2b", "project": "myapp", "env": "prod", "service": "cleanup_job"}
            
            >>> DeploymentNaming.parse_container_name("invalid")
            None
        """
        parts = container_name.split('_')
        
        # Minimum: ws_project_env_service (4 parts)
        if len(parts) < 4:
            return None
        
        # Format: {ws_short}_{project}_{env}_{service}
        # Service is everything after the third underscore (may contain underscores)
        return {
            "workspace_short": parts[0],
            "project": parts[1],
            "env": parts[2],
            "service": '_'.join(parts[3:])  # Join remaining parts for service name
        }

    @staticmethod
    def get_image_name(
        docker_hub_user: str, workspace_id: str, project: str, env: str,
        service_name: str, version: str = "latest"
    ) -> str:
        """Generate Docker image name for registry.

        Format: ``<docker_hub_user>/<ws_short>-<project>-<env>-<service>:<version>``

        Examples:
        - get_image_name("alice", "7f3a2b...", "myproj", "dev", "api") -> "alice/7f3a2b-myproj-dev-api:latest"
        """
        ws_short = DeploymentNaming.get_workspace_short(workspace_id)
        effective_service = DeploymentNaming.get_service_name(service_name)
        return f"{docker_hub_user}/{ws_short}-{project}-{env}-{effective_service}:{version}".lower()

    @staticmethod
    def get_network_name() -> str:
        """Generate Docker network name.
        
        Returns shared network for all projects/envs.
        This eliminates nginx recreation overhead when switching between projects.
        
        Format: ``deployer_network`` (constant)        
       
        Returns:
            Shared network name
            
        Note: All containers across all projects/envs now share one Docker network.
        This is safe because:
        - Containers have unique names (ws_project_env_service)
        - Internal ports are hashed to avoid collisions
        - VPC network provides server-to-server security
        - Nginx configs are project/env/service specific
        """
        return "deployer_network"

    @staticmethod
    def get_dockerfile_name(workspace_id: str, project: str, env: str, service_name: str) -> str:
        """Generate Dockerfile name with project/env/service discrimination.

        Format: ``Dockerfile.<ws_short>-<project>-<env>-<service>``

        Examples:
        - get_dockerfile_name("7f3a2b...", "proj", "dev", "api") -> "Dockerfile.7f3a2b-proj-dev-api"
        """
        ws_short = DeploymentNaming.get_workspace_short(workspace_id)
        effective_service = DeploymentNaming.get_service_name(service_name)
        return f"Dockerfile.{ws_short}-{project}-{env}-{effective_service}"

    @staticmethod
    def get_nginx_config_name(workspace_id: str, project: str, env: str, service_name: str) -> str:
        """Generate nginx configuration file name for a service.

        Format: ``nginx-<ws_short>-<project>_<env>_<service>.conf``

        Example:
        - get_nginx_config_name("7f3a2b...", "proj", "dev", "api") -> "nginx-7f3a2b-proj_dev_api.conf"
        """
        ws_short = DeploymentNaming.get_workspace_short(workspace_id)
        effective_service = DeploymentNaming.get_service_name(service_name)
        return f"nginx-{ws_short}-{project}_{env}_{effective_service}.conf"

    @staticmethod
    def get_all_names(
        docker_hub_user: str, workspace_id: str, project: str, env: str,
        service_name: str, version: str = "latest"
    ) -> Dict[str, str]:
        """Get all naming artifacts for a service in one call.

        Returns a dict with:
        - service_name
        - container_name
        - image_name
        - dockerfile_name
        - network_name
        - nginx_config_name
        """
        return {
            "service_name": DeploymentNaming.get_service_name(service_name),
            "container_name": DeploymentNaming.get_container_name(workspace_id, project, env, service_name),
            "image_name": DeploymentNaming.get_image_name(docker_hub_user, workspace_id, project, env, service_name, version),
            "dockerfile_name": DeploymentNaming.get_dockerfile_name(workspace_id, project, env, service_name),
            "network_name": DeploymentNaming.get_network_name(),
            "nginx_config_name": DeploymentNaming.get_nginx_config_name(workspace_id, project, env, service_name),
        }
    
    # Legacy aliases for backward compatibility (deprecated - use workspace_id versions)
    @staticmethod
    def get_container_name_legacy(user: str, project: str, env: str, service_name: str) -> str:
        """DEPRECATED: Use get_container_name with workspace_id instead."""
        return DeploymentNaming.get_container_name(user, project, env, service_name)


class PortResolver:
    """
    Port resolution for deployment system with toggle support.
    
    For zero-downtime deployments:
    - Base port: deterministic port in 8000-8999 range
    - Secondary port: base_port + 10000 (18000-18999 range)
    - Internal port: stable port in 5000-5999 range (never changes)
    
    The internal port is what other services connect to via nginx.
    It stays constant regardless of which backend (base/secondary) is active.
    """
    
    SECONDARY_PORT_OFFSET = 10000
    
    @staticmethod
    def get_host_port(
        workspace_id: str, project: str, env: str, service_name: str,
        container_port: str = "8000", base_port: int = 8000
    ) -> int:
        """
        Generate deterministic host port for mapping.
        
        This is the BASE port used in toggle deployments.
        Secondary deployments use base_port + 10000.
        
        Args:
            workspace_id: Workspace/user ID
            project: Project name
            env: Environment
            service_name: Service name
            container_port: Container port being mapped
            base_port: Base port range start (default: 8000)
            
        Returns:
            Base host port (8000-8999 range)
        """
        import hashlib
        ws_short = DeploymentNaming.get_workspace_short(workspace_id)
        hash_input = f"{ws_short}_{project}_{env}_{service_name}_{container_port}"
        hash_value = int(hashlib.md5(hash_input.encode()).hexdigest()[:8], 16)
        port_offset = hash_value % 1000
        return base_port + port_offset
    
    @staticmethod
    def get_secondary_port(base_port: int) -> int:
        """Get secondary port from base port (+10000)."""
        return base_port + PortResolver.SECONDARY_PORT_OFFSET
    
    @staticmethod
    def is_secondary_port(port: int) -> bool:
        """Check if port is a secondary (toggle) port."""
        return port >= 18000 and port < 19000
    
    @staticmethod
    def get_base_port(port: int) -> int:
        """Get base port from either base or secondary port."""
        if PortResolver.is_secondary_port(port):
            return port - PortResolver.SECONDARY_PORT_OFFSET
        return port
    
    @staticmethod
    def get_internal_port(
        workspace_id: str, project: str, env: str, service_name: str,
        base_port: int = 5000
    ) -> int:
        """
        Generate deterministic INTERNAL port for nginx to listen on.
        
        This port is STABLE across deployments and toggle states.
        Apps always connect to localhost:INTERNAL_PORT regardless of backend changes.
        
        The hash input does NOT include container_port or version - only project/env/service.
        This ensures the internal port never changes for a given service.
        
        Args:
            workspace_id: Workspace/user ID
            project: Project name
            env: Environment
            service_name: Service name
            base_port: Internal port range start (default: 5000)
            
        Returns:
            Internal port (5000-5999 range)
        """
        import hashlib
        ws_short = DeploymentNaming.get_workspace_short(workspace_id)
        hash_input = f"{ws_short}_{project}_{env}_{service_name}_internal"
        hash_value = int(hashlib.md5(hash_input.encode()).hexdigest()[:8], 16)
        port_offset = hash_value % 1000
        return base_port + port_offset