from typing import Dict


class DeploymentNaming:
    """Centralized naming functions for all deployment artifacts (no proxy flag)."""

    @staticmethod
    def get_service_name(service_name: str) -> str:
        """Return the service name unchanged.

        This method exists for symmetry with other helpers and future extension.
        """
        return service_name

    @staticmethod
    def get_container_name(project: str, env: str, service_name: str) -> str:
        """Generate Docker container name using the standard convention.

        Format: ``<project>_<env>_<service>``

        Examples:
        - get_container_name("myproj", "dev", "api") -> "myproj_dev_api"
        - get_container_name("myproj", "prod", "web") -> "myproj_prod_web"
        """
        effective_service = DeploymentNaming.get_service_name(service_name)
        return f"{project}_{env}_{effective_service}"

    @staticmethod
    def get_container_name_pattern(project: str, env: str, service_name: str) -> str:
        """
        Generate wildcard pattern for finding service containers (both primary and secondary).
        
        Used to find all containers for a service regardless of toggle state.
        
        Format: ``<project>_<env>_<service>*``
        
        Matches:
        - myproj_dev_api (primary)
        - myproj_dev_api_secondary (secondary)
        
        Args:
            project: Project name
            env: Environment name
            service_name: Service name
            
        Returns:
            Pattern string for container name matching
            
        Examples:
            get_container_name_pattern("myproj", "dev", "api") -> "myproj_dev_api*"
        """
        base_name = DeploymentNaming.get_container_name(project, env, service_name)
        return f"{base_name}*"

    @staticmethod
    def get_image_name(
        docker_hub_user: str, project: str, env: str,
        service_name: str, version: str = "latest"
    ) -> str:
        """Generate Docker image name for registry.

        Format: ``<docker_hub_user>/<project>-<env>-<service>:<version>``

        Examples:
        - get_image_name("alice", "myproj", "dev", "api") -> "alice/myproj-dev-api:latest"
        - get_image_name("bob", "proj", "staging", "web", "1.2.3") -> "bob/proj-staging-web:1.2.3"
        """
        effective_service = DeploymentNaming.get_service_name(service_name)
        return f"{docker_hub_user}/{project}-{env}-{effective_service}:{version}"

    @staticmethod
    def get_network_name(project: str, env: str) -> str:
        """Generate Docker network name.
        
        REFACTORED: Now returns shared network for all projects/envs.
        This eliminates nginx recreation overhead when switching between projects.
        
        Format: ``deployer_network`` (constant)
        
        Args:
            project: Project name (unused, kept for API compatibility)
            env: Environment name (unused, kept for API compatibility)
        
        Returns:
            Shared network name
        
        Examples:
            - get_network_name("myproj", "dev") -> "deployer_network"
            - get_network_name("another", "prod") -> "deployer_network"
            
        Note: All containers across all projects/envs now share one Docker network.
        This is safe because:
        - Containers have unique names (project_env_service)
        - Internal ports are hashed to avoid collisions
        - VPC network provides server-to-server security
        - Nginx configs are project/env/service specific
        """
        return "deployer_network"  # CHANGED: was f"{project}_{env}_network"

    @staticmethod
    def get_dockerfile_name(project: str, env: str, service_name: str) -> str:
        """Generate Dockerfile name with project/env/service discrimination.

        Format: ``Dockerfile.<project>-<env>-<service>``

        Examples:
        - get_dockerfile_name("proj", "dev", "api") -> "Dockerfile.proj-dev-api"
        - get_dockerfile_name("proj", "prod", "nginx") -> "Dockerfile.proj-prod-nginx"
        """
        effective_service = DeploymentNaming.get_service_name(service_name)
        return f"Dockerfile.{project}-{env}-{effective_service}"

    @staticmethod
    def get_nginx_config_name(project: str, env: str, service_name: str) -> str:
        """Generate nginx configuration file name for a service.

        Format: ``nginx-<project>_<env>_<service>.conf``

        Example:
        - get_nginx_config_name("proj", "dev", "api") -> "nginx-proj_dev_api.conf"
        """
        effective_service = DeploymentNaming.get_service_name(service_name)
        return f"nginx-{project}_{env}_{effective_service}.conf"

    @staticmethod
    def get_all_names(
        docker_hub_user: str, project: str, env: str,
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
            "container_name": DeploymentNaming.get_container_name(project, env, service_name),
            "image_name": DeploymentNaming.get_image_name(docker_hub_user, project, env, service_name, version),
            "dockerfile_name": DeploymentNaming.get_dockerfile_name(project, env, service_name),
            "network_name": DeploymentNaming.get_network_name(project, env),
            "nginx_config_name": DeploymentNaming.get_nginx_config_name(project, env, service_name),
        }