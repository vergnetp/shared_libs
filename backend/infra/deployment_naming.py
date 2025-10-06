from typing import Dict


class DeploymentNaming:
    """Centralized naming functions for all deployment artifacts"""

    @staticmethod
    def get_service_name(service_name: str, is_proxy: bool = False) -> str:
        """
        Get the effective service name.

        Logic:
        - If `is_proxy=True` and `service_name` does NOT already end with `-proxy`, append `-proxy`.
        - Otherwise, return `service_name` unchanged.

        Examples:
        - get_service_name("api") → "api"
        - get_service_name("api", is_proxy=True) → "api-proxy"
        - get_service_name("api-proxy", is_proxy=True) → "api-proxy"
        """
        if is_proxy and not service_name.endswith("-proxy"):
            return f"{service_name}-proxy"
        return service_name

    @staticmethod
    def get_container_name(project: str, env: str, service_name: str, is_proxy: bool = False) -> str:
        """
        Generate Docker container name using the standard convention.

        Logic:
        - First resolve `effective_service` with `get_service_name()`.
        - Format: `<project>_<env>_<effective_service>`

        Examples:
        - get_container_name("myproj", "dev", "api") → "myproj_dev_api"
        - get_container_name("myproj", "prod", "api", is_proxy=True) → "myproj_prod_api-proxy"
        """
        effective_service = DeploymentNaming.get_service_name(service_name, is_proxy)
        return f"{project}_{env}_{effective_service}"

    @staticmethod
    def get_image_name(
        docker_hub_user: str, project: str, env: str,
        service_name: str, version: str = "latest", is_proxy: bool = False
    ) -> str:
        """
        Generate Docker image name for registry.

        Logic:
        - First resolve `effective_service` with `get_service_name()`.
        - Format: `<docker_hub_user>/<project>-<env>-<effective_service>:<version>`

        Examples:
        - get_image_name("alice", "myproj", "dev", "api") → "alice/myproj-dev-api:latest"
        - get_image_name("bob", "proj", "staging", "web", "1.2.3", is_proxy=True) → "bob/proj-staging-web-proxy:1.2.3"
        """
        effective_service = DeploymentNaming.get_service_name(service_name, is_proxy)
        return f"{docker_hub_user}/{project}-{env}-{effective_service}:{version}"

    @staticmethod
    def get_network_name(project: str, env: str) -> str:
        """
        Generate Docker network name.

        Logic:
        - Format: `<project>_<env>_network`

        Example:
        - get_network_name("myproj", "dev") → "myproj_dev_network"
        """
        return f"{project}_{env}_network"

    @staticmethod
    def get_dockerfile_name(project: str, env: str, service_name: str, is_proxy: bool = False) -> str:
        """
        Generate Dockerfile name with project/env/service discrimination.

        Logic:
        - First resolve `effective_service` with `get_service_name()`.
        - Format: `Dockerfile.<project>-<env>-<effective_service>`

        Examples:
        - get_dockerfile_name("proj", "dev", "api") → "Dockerfile.proj-dev-api"
        - get_dockerfile_name("proj", "prod", "nginx", is_proxy=True) → "Dockerfile.proj-prod-nginx-proxy"
        """
        effective_service = DeploymentNaming.get_service_name(service_name, is_proxy)
        return f"Dockerfile.{project}-{env}-{effective_service}"

    @staticmethod
    def get_nginx_config_name(project: str, env: str, service_name: str) -> str:
        """
        Generate nginx configuration file name for a proxy service.

        Logic:
        - Always treat the service as a proxy (force `is_proxy=True`).
        - Format: `nginx-<project>_<env>_<effective_service>.conf`

        Examples:
        - get_nginx_config_name("proj", "dev", "api") → "nginx-proj_dev_api-proxy.conf"
        """
        effective_service = DeploymentNaming.get_service_name(service_name, is_proxy=True)
        return f"nginx-{project}_{env}_{effective_service}.conf"

    @staticmethod
    def get_all_names(
        docker_hub_user: str, project: str, env: str,
        service_name: str, version: str = "latest", is_proxy: bool = False
    ) -> Dict[str, str]:
        """
        Get all naming artifacts for a service in one call.

        Example:
         get_all_names("alice", "proj", "dev", "api", "1.0.0", is_proxy=True) →  
         {  
           "service_name": "api-proxy",  
            "container_name": "proj_dev_api-proxy",  
            "image_name": "alice/proj-dev-api-proxy:1.0.0",  
            "dockerfile_name": "Dockerfile.proj-dev-api-proxy",  
            "network_name": "proj_dev_network",  
            "nginx_config_name": "nginx-proj_dev_api-proxy.conf" # only if is_proxy is True  
         }  
        """
        result = {
            "service_name": DeploymentNaming.get_service_name(service_name, is_proxy),
            "container_name": DeploymentNaming.get_container_name(project, env, service_name, is_proxy),
            "image_name": DeploymentNaming.get_image_name(docker_hub_user, project, env, service_name, version, is_proxy),
            "dockerfile_name": DeploymentNaming.get_dockerfile_name(project, env, service_name, is_proxy),
            "network_name": DeploymentNaming.get_network_name(project, env),
        }
        if is_proxy:
            result["nginx_config_name"] = DeploymentNaming.get_nginx_config_name(project, env, service_name)
        return result
