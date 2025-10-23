from typing import Optional, Dict, List, Any, Union

try:
    from .deployment_config import DeploymentConfigurer
except ImportError:
    from deployment_config import DeploymentConfigurer
try:
    from .logger import Logger
except ImportError:
    from logger import Logger


def log(msg):
    Logger.log(msg)


class ProjectManager:
    """
    Manages project configuration files - create, update, delete projects and services.
    
    This class handles the configuration layer only (JSON files).
    For deployment operations, use ProjectDeployer or UnifiedDeployer.
    """
    
    @staticmethod
    def create_project(
        name: str,
        docker_hub_user: str = None,
        version: str = "latest",
        default_server_ip: str = "localhost"
    ) -> bool:
        """
        Create new project configuration.
        
        Args:
            name: Project name
            docker_hub_user: Docker Hub username
            version: Default version tag
            default_server_ip: Default server IP
            
        Returns:
            True if created successfully
        """
        config = DeploymentConfigurer(name, create_if_missing=True)
        
        config.raw_config = {
            "project": {
                "name": name,
                "docker_hub_user": docker_hub_user,
                "version": version,
                "default_server_ip": default_server_ip,
                "services": {}
            }
        }
        
        config.save_config()
        return True
    
    @staticmethod
    def update_project(
        name: str,
        docker_hub_user: Optional[str] = None,
        version: Optional[str] = None,
        default_server_ip: Optional[str] = None
    ) -> bool:
        """Update project-level configuration"""
        config = DeploymentConfigurer(name)
        
        if docker_hub_user:
            config.raw_config["project"]["docker_hub_user"] = docker_hub_user
        if version:
            config.raw_config["project"]["version"] = version
        if default_server_ip:
            config.raw_config["project"]["default_server_ip"] = default_server_ip
        
        config.save_config()
        return True
    
    @staticmethod
    def delete_project(name: str) -> bool:
        """Delete project configuration"""
        config = DeploymentConfigurer(name)
        config.config_file.unlink()
        return True
    
    @staticmethod
    def _calculate_startup_order(project_name: str, depends_on: List[str]) -> int:
        """
        Calculate startup_order based on dependencies.
        
        Args:
            project_name: Project name
            depends_on: List of service names this service depends on
            
        Returns:
            Calculated startup_order (max of dependencies + 1)
        """
        try:
            config = DeploymentConfigurer(project_name)
            services = config.raw_config.get("project", {}).get("services", {})
            
            max_order = 0
            for dep_service in depends_on:
                if dep_service in services:
                    dep_order = services[dep_service].get("startup_order", 1)
                    max_order = max(max_order, dep_order)
                else:
                    log(f"Warning: Dependency '{dep_service}' not found, assuming startup_order=1")
                    max_order = max(max_order, 1)
            
            return max_order + 1
            
        except Exception as e:
            log(f"Warning: Could not calculate startup_order from dependencies: {e}")
            return 2  # Safe default
    
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
        auto_scaling: Optional[Union[bool, Dict[str, Any]]] = None,
        **other_config
    ) -> bool:
        """Adds service to project config"""
        config = DeploymentConfigurer(project_name)
        
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
        if auto_scaling is not None:
            service_config["auto_scaling"] = auto_scaling
        
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
        depends_on: Optional[List[str]] = None,
        **other_config
    ) -> bool:
        """Adds PostgreSQL service to project"""
        try:
            config = DeploymentConfigurer(project_name)
        except FileNotFoundError:
            ProjectManager.create_project(project_name)
            config = DeploymentConfigurer(project_name)
        
        # Calculate startup_order
        if 'startup_order' in other_config:
            startup_order = other_config.pop('startup_order')
        elif depends_on:
            startup_order = ProjectManager._calculate_startup_order(project_name, depends_on)
            other_config['depends_on'] = depends_on
        else:
            startup_order = 1  # Default for databases
        
        service_config = {
            "image": f"postgres:{version}",
            "env_vars": {
                "POSTGRES_DB": f"{project_name}_{{hash}}",
                "POSTGRES_USER": f"{project_name}_user",
                "POSTGRES_PASSWORD_FILE": "/run/secrets/db_password"
            },
            "startup_order": startup_order,
            "server_zone": server_zone,
            "servers_count": servers_count,
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
        depends_on: Optional[List[str]] = None,
        **other_config
    ) -> bool:
        """Adds Redis service to project"""
        try:
            config = DeploymentConfigurer(project_name)
        except FileNotFoundError:
            ProjectManager.create_project(project_name)
            config = DeploymentConfigurer(project_name)
        
        # Calculate startup_order
        if 'startup_order' in other_config:
            startup_order = other_config.pop('startup_order')
        elif depends_on:
            startup_order = ProjectManager._calculate_startup_order(project_name, depends_on)
            other_config['depends_on'] = depends_on
        else:
            startup_order = 1
        
        service_config = {
            "image": f"redis:{version}",
            "command": ["redis-server", "--requirepass", "$(cat /run/secrets/redis_password)"],
            "startup_order": startup_order,
            "server_zone": server_zone,
            "servers_count": servers_count,
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
        depends_on: Optional[List[str]] = None,
        **other_config
    ) -> bool:
        """Adds OpenSearch service to project"""
        try:
            config = DeploymentConfigurer(project_name)
        except FileNotFoundError:
            ProjectManager.create_project(project_name)
            config = DeploymentConfigurer(project_name)
        
        # Calculate startup_order
        if 'startup_order' in other_config:
            startup_order = other_config.pop('startup_order')
        elif depends_on:
            startup_order = ProjectManager._calculate_startup_order(project_name, depends_on)
            other_config['depends_on'] = depends_on
        else:
            startup_order = 1
        
        service_config = {
            "image": f"opensearchproject/opensearch:{version}",
            "env_vars": {
                "discovery.type": "single-node",
                "OPENSEARCH_INITIAL_ADMIN_PASSWORD": "$(cat /run/secrets/opensearch_password)"
            },
            "startup_order": startup_order,
            "server_zone": server_zone,
            "servers_count": servers_count,
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
        depends_on: Optional[List[str]] = None,
        **other_config
    ) -> bool:
        """Adds Nginx service to project"""
        try:
            config = DeploymentConfigurer(project_name)
        except FileNotFoundError:
            ProjectManager.create_project(project_name)
            config = DeploymentConfigurer(project_name)
        
        # Calculate startup_order
        if 'startup_order' in other_config:
            startup_order = other_config.pop('startup_order')
        elif depends_on:
            startup_order = ProjectManager._calculate_startup_order(project_name, depends_on)
            other_config['depends_on'] = depends_on
        else:
            startup_order = 10  # Nginx typically starts last
        
        service_config = {
            "image": f"nginx:{version}",
            "startup_order": startup_order,
            "server_zone": server_zone,
            "servers_count": servers_count,
            **other_config
        }
        
        config.raw_config["project"]["services"]["nginx"] = service_config
        config.save_config()
        return True