import os
import json
from typing import Optional, Dict

import constants
from deployment_config import DeploymentConfigurer

class ProjectManager:
    """High-level API for managing project configurations"""
    
    @staticmethod
    def create_project(
        name: str,
        docker_hub_user: str = None,
        version: str = "latest",
        default_server_ip: str = "localhost"
    ) -> bool:
        """Creates new project config"""
        # Check if already exists
        try:
            DeploymentConfigurer(name)
            raise ValueError(f"Project '{name}' already exists")
        except FileNotFoundError:
            pass  # Good, doesn't exist
        
        docker_hub_user = docker_hub_user or os.getenv("DOCKER_HUB_USER", "default_user")
        
        # Create minimal config file directly
        config_path = constants.get_project_config_path(name)
        config_path.parent.mkdir(exist_ok=True, parents=True)
        
        raw_config = {
            "project": {
                "name": name,
                "docker_hub_user": docker_hub_user,
                "version": version,
                "default_server_ip": default_server_ip,
                "services": {}
            }
        }
        
        with config_path.open("w") as f:
            json.dump(raw_config, f, indent=4)
        
        return True
    
    @staticmethod
    def update_project(
        name: str,
        docker_hub_user: Optional[str] = None,
        version: Optional[str] = None,
        default_server_ip: Optional[str] = None
    ) -> bool:
        """Updates project-level config"""
        config = DeploymentConfigurer(name)
        
        if docker_hub_user is not None:
            config.raw_config["project"]["docker_hub_user"] = docker_hub_user
        if version is not None:
            config.raw_config["project"]["version"] = version
        if default_server_ip is not None:
            config.raw_config["project"]["default_server_ip"] = default_server_ip
        
        config.save_config()
        return True
    
    @staticmethod
    def delete_project(name: str) -> bool:
        """Deletes project config file"""
        config_path = constants.get_project_config_path(name)
        if not config_path.exists():
            raise FileNotFoundError(f"Project '{name}' does not exist")
        
        config_path.unlink()
        return True
    
    @staticmethod
    def add_service(
        project_name: str,
        service_name: str,
        startup_order: int = 1,
        server_zone: str = "lon1",
        servers_count: int = 1,
        dockerfile: Optional[str] = None,
        dockerfile_content: Optional[Dict[str, str]] = None,
        image: Optional[str] = None,
        build_context: Optional[str] = None,
        **other_config
    ) -> bool:
        """Adds service to project config"""
        # Try to load, or create if doesn't exist
        try:
            config = DeploymentConfigurer(project_name)
        except FileNotFoundError:
            ProjectManager.create_project(project_name)
            config = DeploymentConfigurer(project_name)
        
        # Validate service doesn't exist
        if service_name in config.raw_config["project"]["services"]:
            raise ValueError(f"Service '{service_name}' already exists")
        
        # Build service config
        service_config = {
            "startup_order": startup_order,
            "server_zone": server_zone,
            "servers_count": servers_count,
            **other_config
        }
        
        if dockerfile:
            service_config["dockerfile"] = dockerfile
        if dockerfile_content:
            service_config["dockerfile_content"] = dockerfile_content
        if image:
            service_config["image"] = image
        if build_context:
            service_config["build_context"] = build_context
        
        config.raw_config["project"]["services"][service_name] = service_config
        config.save_config()
        return True
    
    @staticmethod
    def update_service(
        project_name: str,
        service_name: str,
        **updates
    ) -> bool:
        """Updates existing service config"""
        config = DeploymentConfigurer(project_name)
        
        if service_name not in config.raw_config["project"]["services"]:
            raise ValueError(f"Service '{service_name}' does not exist")
        
        config.raw_config["project"]["services"][service_name].update(updates)
        config.save_config()
        return True
    
    @staticmethod
    def delete_service(
        project_name: str,
        service_name: str
    ) -> bool:
        """Removes service from project config"""
        config = DeploymentConfigurer(project_name)
        
        if service_name not in config.raw_config["project"]["services"]:
            raise ValueError(f"Service '{service_name}' does not exist")
        
        del config.raw_config["project"]["services"][service_name]
        config.save_config()
        return True

    @staticmethod
    def add_postgres(
        project_name: str,
        version: str = "15",
        server_zone: str = "lon1",
        servers_count: int = 1,
        startup_order: int = 1,
        **other_config
    ) -> bool:
        """Adds PostgreSQL service to project"""
        try:
            config = DeploymentConfigurer(project_name)
        except FileNotFoundError:
            ProjectManager.create_project(project_name)
            config = DeploymentConfigurer(project_name)
        
        if "postgres" in config.raw_config["project"]["services"]:
            raise ValueError(f"Service 'postgres' already exists")
        
        service_config = {
            "image": f"postgres:{version}",
            "server_zone": server_zone,
            "servers_count": servers_count,
            "startup_order": startup_order,
            **other_config
        }
        
        config.raw_config["project"]["services"]["postgres"] = service_config
        config.save_config()
        return True
    

    @staticmethod
    def add_redis(
        project_name: str,
        version: str = "7-alpine",
        server_zone: str = "lon1",
        servers_count: int = 1,
        startup_order: int = 1,
        **other_config
    ) -> bool:
        """Adds Redis service to project"""
        try:
            config = DeploymentConfigurer(project_name)
        except FileNotFoundError:
            ProjectManager.create_project(project_name)
            config = DeploymentConfigurer(project_name)
        
        if "redis" in config.raw_config["project"]["services"]:
            raise ValueError(f"Service 'redis' already exists")
        
        service_config = {
            "image": f"redis:{version}",
            "server_zone": server_zone,
            "servers_count": servers_count,
            "startup_order": startup_order,
            **other_config
        }
        
        config.raw_config["project"]["services"]["redis"] = service_config
        config.save_config()
        return True

    @staticmethod
    def add_opensearch(
        project_name: str,
        version: str = "2",
        server_zone: str = "lon1",
        servers_count: int = 1,
        startup_order: int = 1,
        **other_config
    ) -> bool:
        """Adds OpenSearch service to project"""
        try:
            config = DeploymentConfigurer(project_name)
        except FileNotFoundError:
            ProjectManager.create_project(project_name)
            config = DeploymentConfigurer(project_name)
        
        if "opensearch" in config.raw_config["project"]["services"]:
            raise ValueError(f"Service 'opensearch' already exists")
        
        service_config = {
            "image": f"opensearchproject/opensearch:{version}",
            "server_zone": server_zone,
            "servers_count": servers_count,
            "startup_order": startup_order,
            **other_config
        }
        
        config.raw_config["project"]["services"]["opensearch"] = service_config
        config.save_config()
        return True

    @staticmethod
    def add_nginx(
        project_name: str,
        version: str = "alpine",
        server_zone: str = "lon1",
        servers_count: int = 1,
        startup_order: int = 10,
        **other_config
    ) -> bool:
        """Adds Nginx service to project"""
        try:
            config = DeploymentConfigurer(project_name)
        except FileNotFoundError:
            ProjectManager.create_project(project_name)
            config = DeploymentConfigurer(project_name)
        
        if "nginx" in config.raw_config["project"]["services"]:
            raise ValueError(f"Service 'nginx' already exists")
        
        service_config = {
            "image": f"nginx:{version}",
            "server_zone": server_zone,
            "servers_count": servers_count,
            "startup_order": startup_order,
            **other_config
        }
        
        config.raw_config["project"]["services"]["nginx"] = service_config
        config.save_config()
        return True