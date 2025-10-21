import os
from typing import Optional, Dict, List, Any
from project_manager import ProjectManager
from global_deployer import UnifiedDeployer


class ProjectDeployer:
    """
    Unified interface for complete project lifecycle: create, configure, deploy, manage.
    
    Combines configuration management (ProjectManager) with deployment operations (UnifiedDeployer)
    into a single, fluent API.
    
    Usage Examples:
        # Create and configure new project
        project = ProjectDeployer.create("myapp")
        project.add_postgres(version="15", servers_count=2) \\
               .add_redis() \\
               .add_service("api", 
                           dockerfile_content={"1": "FROM python:3.11", ...},
                           build_context="./api",
                           servers_count=3)
        
        # Load existing project
        project = ProjectDeployer("myapp")
        
        # Deploy
        project.deploy(env="prod", zones=["lon1", "nyc3"])
        
        # Monitor and manage
        project.status()
        project.logs(service="api", env="prod")
        project.rollback(env="prod", service="api")
    """
    
    def __init__(self, project_name: str):
        """
        Load existing project.
        
        Args:
            project_name: Name of existing project
            
        Raises:
            FileNotFoundError: If project doesn't exist
        """
        self.project_name = project_name
        self._deployer = UnifiedDeployer(project_name)
    
    # =========================================================================
    # PROJECT LIFECYCLE - Configuration Management
    # =========================================================================
    
    @staticmethod
    def create(
        name: str,
        docker_hub_user: str = None,
        version: str = "latest",
        default_server_ip: str = "localhost"
    ) -> 'ProjectDeployer':
        """
        Create new project and return instance for chaining.
        
        Args:
            name: Project name
            docker_hub_user: Docker Hub username (default: from DOCKER_HUB_USER env var)
            version: Default version tag (default: "latest")
            default_server_ip: Default server IP (default: "localhost")
            
        Returns:
            ProjectDeployer instance
            
        Raises:
            ValueError: If project already exists
            
        Example:
            project = ProjectDeployer.create("myapp", docker_hub_user="john")
        """
        ProjectManager.create_project(name, docker_hub_user, version, default_server_ip)
        return ProjectDeployer(name)
    
    @staticmethod
    def list_projects() -> List[str]:
        """
        List all available projects.
        
        Returns:
            List of project names
            
        Example:
            projects = ProjectDeployer.list_projects()
            # ['myapp', 'another-project', ...]
        """
        from deployment_config import DeploymentConfigurer
        return DeploymentConfigurer.list_projects()
    
    def update_config(
        self,
        docker_hub_user: Optional[str] = None,
        version: Optional[str] = None,
        default_server_ip: Optional[str] = None
    ) -> 'ProjectDeployer':
        """
        Update project-level configuration (fluent API).
        
        Args:
            docker_hub_user: Docker Hub username
            version: Version tag
            default_server_ip: Default server IP
            
        Returns:
            Self for chaining
            
        Example:
            project.update_config(version="v2.0.0").deploy(env="prod")
        """
        ProjectManager.update_project(
            self.project_name,
            docker_hub_user,
            version,
            default_server_ip
        )
        return self
    
    def delete(self) -> bool:
        """
        Delete project configuration.
        
        Returns:
            True if deleted successfully
            
        Example:
            project.delete()
        """
        return ProjectManager.delete_project(self.project_name)
    
    # =========================================================================
    # SERVICE MANAGEMENT - Generic
    # =========================================================================
    
    def add_service(
            self,
            service_name: str,
            startup_order: int = 1,
            server_zone: str = "lon1",
            servers_count: int = 1,
            dockerfile: Optional[str] = None,
            dockerfile_content: Optional[Dict[str, str]] = None,
            image: Optional[str] = None,
            build_context: Optional[str] = None,
            auto_scaling: Optional[bool | Dict[str, Any]] = None,
            **other_config
        ) -> 'ProjectDeployer':
            """
            Add generic service to project (fluent API).
            
            Args:
                service_name: Service name
                startup_order: Startup order (lower starts first)
                server_zone: DigitalOcean zone (e.g., "lon1", "nyc3")
                servers_count: Number of servers/replicas
                dockerfile: Path to Dockerfile (relative to build_context)
                dockerfile_content: Inline Dockerfile as dict {"1": "FROM...", "2": "WORKDIR..."}
                image: Pre-built image (e.g., "nginx:alpine")
                build_context: Build context path
                auto_scaling: Enable auto-scaling. Can be:
                    - True: Enable both vertical and horizontal with defaults
                    - False/None: Disable (default)
                    - Dict: Custom config with "vertical" and/or "horizontal" keys
                    Example: {"vertical": {"cpu_scale_up": 80}, "horizontal": {"rps_scale_up": 1000}}
                **other_config: Additional service config (env_vars, volumes, etc.)
                
            Returns:
                Self for chaining
                
            Example:
                # Enable with defaults
                project.add_service("api", auto_scaling=True)
                
                # Custom thresholds
                project.add_service("api", auto_scaling={
                    "vertical": {"cpu_scale_up": 80, "cpu_scale_down": 25},
                    "horizontal": {"rps_scale_up": 1000}
                })
                
                # Only horizontal scaling
                project.add_service("api", auto_scaling={"horizontal": {}})
            """
            ProjectManager.add_service(
                self.project_name,
                service_name,
                startup_order,
                server_zone,
                servers_count,
                dockerfile,
                dockerfile_content,
                image,
                build_context,
                auto_scaling=auto_scaling,
                **other_config
            )
            return self
    
    def update_service(
        self,
        service_name: str,
        **updates
    ) -> 'ProjectDeployer':
        """
        Update existing service configuration (fluent API).
        
        Args:
            service_name: Service to update
            **updates: Fields to update
            
        Returns:
            Self for chaining
            
        Example:
            project.update_service("api", servers_count=5, domain="new.domain.com")
        """
        ProjectManager.update_service(self.project_name, service_name, **updates)
        return self
    
    def delete_service(self, service_name: str) -> bool:
        """
        Remove service from project.
        
        Args:
            service_name: Service to delete
            
        Returns:
            True if deleted successfully
            
        Example:
            project.delete_service("old-api")
        """
        return ProjectManager.delete_service(self.project_name, service_name)
    
    # =========================================================================
    # SERVICE MANAGEMENT - Convenience Methods
    # =========================================================================
    
    def add_postgres(
        self,
        version: str = "15",
        server_zone: str = "lon1",
        servers_count: int = 1,
        startup_order: int = 1,
        **other_config
    ) -> 'ProjectDeployer':
        """
        Add PostgreSQL database service (fluent API).
        
        Args:
            version: PostgreSQL version (default: "15")
            server_zone: DigitalOcean zone
            servers_count: Number of replicas
            startup_order: Startup order (default: 1 - starts first)
            **other_config: Additional config (env_vars, etc.)
            
        Returns:
            Self for chaining
            
        Example:
            project.add_postgres(version="15", servers_count=2)
        """
        ProjectManager.add_postgres(
            self.project_name,
            version,
            server_zone,
            servers_count,
            startup_order,
            **other_config
        )
        return self
    
    def add_redis(
        self,
        version: str = "7-alpine",
        server_zone: str = "lon1",
        servers_count: int = 1,
        startup_order: int = 1,
        **other_config
    ) -> 'ProjectDeployer':
        """
        Add Redis cache service (fluent API).
        
        Args:
            version: Redis version (default: "7-alpine")
            server_zone: DigitalOcean zone
            servers_count: Number of replicas
            startup_order: Startup order (default: 1 - starts first)
            **other_config: Additional config (env_vars, etc.)
            
        Returns:
            Self for chaining
            
        Example:
            project.add_redis(version="7-alpine", servers_count=1)
        """
        ProjectManager.add_redis(
            self.project_name,
            version,
            server_zone,
            servers_count,
            startup_order,
            **other_config
        )
        return self
    
    def add_opensearch(
        self,
        version: str = "2",
        server_zone: str = "lon1",
        servers_count: int = 1,
        startup_order: int = 1,
        **other_config
    ) -> 'ProjectDeployer':
        """
        Add OpenSearch service (fluent API).
        
        Args:
            version: OpenSearch version (default: "2")
            server_zone: DigitalOcean zone
            servers_count: Number of replicas
            startup_order: Startup order (default: 1 - starts first)
            **other_config: Additional config (env_vars, etc.)
            
        Returns:
            Self for chaining
            
        Example:
            project.add_opensearch(version="2", servers_count=1)
        """
        ProjectManager.add_opensearch(
            self.project_name,
            version,
            server_zone,
            servers_count,
            startup_order,
            **other_config
        )
        return self
    
    def add_nginx(
        self,
        version: str = "alpine",
        server_zone: str = "lon1",
        servers_count: int = 1,
        startup_order: int = 10,
        **other_config
    ) -> 'ProjectDeployer':
        """
        Add Nginx web server service (fluent API).
        
        Args:
            version: Nginx version (default: "alpine")
            server_zone: DigitalOcean zone
            servers_count: Number of replicas
            startup_order: Startup order (default: 10 - starts after backends)
            **other_config: Additional config (env_vars, etc.)
            
        Returns:
            Self for chaining
            
        Example:
            project.add_nginx(version="alpine", servers_count=2)
        """
        ProjectManager.add_nginx(
            self.project_name,
            version,
            server_zone,
            servers_count,
            startup_order,
            **other_config
        )
        return self
    
    # =========================================================================
    # DEPLOYMENT OPERATIONS
    # =========================================================================
    
    def build(self, env: str = None, push: bool = True) -> bool:
        """
        Build Docker images for all services.
        
        Args:
            env: Environment to build (None = all environments)
            push: Push images to registry (required for remote/multi-zone)
            
        Returns:
            True if build successful
            
        Example:
            project.build(env="prod", push=True)
        """
        return self._deployer.build(env, push)
    
    def deploy(
        self,
        env: str,
        zones: List[str] = None,
        service: str = None,
        build: bool = True,
        parallel: bool = True
    ) -> Dict[str, bool]:
        """
        Deploy services - automatically handles single-zone or multi-zone.
        
        Args:
            env: Environment to deploy (e.g., "prod", "dev")
            zones: Target zones (e.g., ["lon1", "nyc3"]). If None, auto-detects from config
            service: Deploy specific service only (None = all services)
            build: Whether to build images before deploying (default: True)
            parallel: Deploy zones in parallel (True) or sequentially (False)
            
        Returns:
            Dict mapping zone -> success/failure
            
        Notes:
            For multi-zone deployment, requires Cloudflare Load Balancer ($5/month)
            and CLOUDFLARE_API_TOKEN in environment variables.
            
        Examples:
            # Auto-detect zones from config
            project.deploy(env="prod")
            
            # Specific zones
            project.deploy(env="prod", zones=["lon1", "nyc3", "sgp1"])
            
            # Specific service
            project.deploy(env="prod", service="api")
            
            # Deploy without rebuilding
            project.deploy(env="prod", build=False)
        """
        return self._deployer.deploy(env, zones, service, build, parallel)
    
    # =========================================================================
    # MONITORING & MANAGEMENT
    # =========================================================================
    
    def status(self, env: str = None) -> Dict[str, Any]:
        """
        Get deployment status across all zones.
        
        Args:
            env: Filter by environment (optional)
            
        Returns:
            Dict with zone-level status information
            
        Example:
            status = project.status(env="prod")
            # {'lon1': {'green': 2, 'blue': 0, 'reserve': 1}, ...}
        """
        return self._deployer.status(env)
    
    def list_deployments(self, env: str = None) -> Dict[str, Any]:
        """
        Get detailed deployment information.
        
        Args:
            env: Filter by environment
            
        Returns:
            Deployment details dictionary
            
        Example:
            deployments = project.list_deployments(env="prod")
        """
        return self._deployer.list_deployments(env)
    
    def print_deployments(self, env: str = None):
        """
        Pretty-print deployment status to console.
        
        Args:
            env: Filter by environment
            
        Example:
            project.print_deployments(env="prod")
        """
        self._deployer.print_deployments(env)
    
    def logs(
        self,
        service: str,
        env: str,
        lines: int = 100
    ) -> str:
        """
        Fetch logs from service containers.
        
        Args:
            service: Service name
            env: Environment
            lines: Number of lines to tail (default: 100)
            
        Returns:
            Log output as string
            
        Example:
            logs = project.logs(service="api", env="prod", lines=50)
        """
        return self._deployer.logs(service, env, lines)
    
    def print_logs(
        self,
        service: str,
        env: str,
        lines: int = 100
    ):
        """
        Fetch and print logs to console.
        
        Args:
            service: Service name
            env: Environment
            lines: Number of lines to tail (default: 100)
            
        Example:
            project.print_logs(service="api", env="prod", lines=50)
        """
        self._deployer.print_logs(service, env, lines)
    
    def rollback(
        self,
        env: str,
        service: str,
        version: str = None
    ) -> bool:
        """
        Rollback service to previous deployment.
        
        Args:
            env: Environment
            service: Service name
            version: Target version (None = previous version)
            
        Returns:
            True if rollback successful
            
        Example:
            # Rollback to previous version
            project.rollback(env="prod", service="api")
            
            # Rollback to specific version
            project.rollback(env="prod", service="api", version="v1.2.3")
        """
        return self._deployer.rollback(env, service, version)
    
    # =========================================================================
    # UTILITY
    # =========================================================================
    
    def __repr__(self) -> str:
        return f"ProjectDeployer('{self.project_name}')"