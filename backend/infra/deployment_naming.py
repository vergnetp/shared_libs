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
    def get_container_name(user: str, project: str, env: str, service_name: str) -> str:
        """Generate Docker container name using the standard convention.

        Format: ``<user>_<project>_<env>_<service>``

        Examples:
        - get_container_name("u1", "myproj", "dev", "api") -> "u1_myproj_dev_api"
        - get_container_name("u1", "myproj", "prod", "web") -> "u1_myproj_prod_web"
        """
        effective_service = DeploymentNaming.get_service_name(service_name)
        return f"{user}_{project}_{env}_{effective_service}"

    @staticmethod
    def get_container_name_pattern(user: str, project: str, env: str, service_name: str) -> str:
        """
        Generate wildcard pattern for finding service containers (both primary and secondary).
        
        Used to find all containers for a service regardless of toggle state.
        
        Format: ``<user>_<project>_<env>_<service>*``
        
        Matches:
        - u1_myproj_dev_api (primary)
        - u1_myproj_dev_api_secondary (secondary)
        
        Args:
            user: user id (e.g. "u1")
            project: Project name
            env: Environment name
            service_name: Service name
            
        Returns:
            Pattern string for container name matching
            
        Examples:
            get_container_name_pattern("u1", "myproj", "dev", "api") -> "u1_myproj_dev_api*"
        """
        base_name = DeploymentNaming.get_container_name(user, project, env, service_name)
        return f"{base_name}*"

    @staticmethod
    def get_image_name(
        docker_hub_user: str, user: str, project: str, env: str,
        service_name: str, version: str = "latest"
    ) -> str:
        """Generate Docker image name for registry.

        Format: ``<docker_hub_user>/<user>-<project>-<env>-<service>:<version>``

        Examples:
        - get_image_name("alice", "u1", "myproj", "dev", "api") -> "alice/u1-myproj-dev-api:latest"
        - get_image_name("bob", "u1", "proj", "staging", "web", "1.2.3") -> "bob/u1-proj-staging-web:1.2.3"
        """
        effective_service = DeploymentNaming.get_service_name(service_name)
        return f"{docker_hub_user}/{user}-{project}-{env}-{effective_service}:{version}"

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
        - Containers have unique names (project_env_service)
        - Internal ports are hashed to avoid collisions
        - VPC network provides server-to-server security
        - Nginx configs are project/env/service specific
        """
        return "deployer_network"

    @staticmethod
    def get_dockerfile_name(user: str, project: str, env: str, service_name: str) -> str:
        """Generate Dockerfile name with project/env/service discrimination.

        Format: ``Dockerfile.<user>-<project>-<env>-<service>``

        Examples:
        - get_dockerfile_name("u1", "proj", "dev", "api") -> "Dockerfile.u1-proj-dev-api"
        - get_dockerfile_name("u1", "proj", "prod", "nginx") -> "Dockerfile.u1-proj-prod-nginx"
        """
        effective_service = DeploymentNaming.get_service_name(service_name)
        return f"Dockerfile.{user}-{project}-{env}-{effective_service}"

    @staticmethod
    def get_nginx_config_name(user: str, project: str, env: str, service_name: str) -> str:
        """Generate nginx configuration file name for a service.

        Format: ``nginx-<user>-<project>_<env>_<service>.conf``

        Example:
        - get_nginx_config_name("u1", "proj", "dev", "api") -> "nginx-u1-proj_dev_api.conf"
        """
        effective_service = DeploymentNaming.get_service_name(service_name)
        return f"nginx-{user}-{project}_{env}_{effective_service}.conf"

    @staticmethod
    def get_all_names(
        docker_hub_user: str, user:str, project: str, env: str,
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
            "container_name": DeploymentNaming.get_container_name(user, project, env, service_name),
            "image_name": DeploymentNaming.get_image_name(docker_hub_user, user, project, env, service_name, version),
            "dockerfile_name": DeploymentNaming.get_dockerfile_name(user, project, env, service_name),
            "network_name": DeploymentNaming.get_network_name(),
            "nginx_config_name": DeploymentNaming.get_nginx_config_name(user, project, env, service_name),
        }