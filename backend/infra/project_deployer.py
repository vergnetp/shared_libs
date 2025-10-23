import os
from typing import Optional, Dict, List, Any, Union

from deployment_config import DeploymentConfigurer
from project_manager import ProjectManager
from global_deployer import UnifiedDeployer
from logger import Logger
from encryption import Encryption
from git_manager import GitManager
from deployer import Deployer
from secrets_rotator import SecretsRotator
from health_monitor import HealthMonitor
from server_inventory import ServerInventory
from live_deployment_query import LiveDeploymentQuery
from do_manager import DOManager
from deployment_state_manager import DeploymentStateManager


def log(msg):
    Logger.log(msg)

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
    
    def _calculate_startup_order(self, depends_on: List[str]) -> int:
        """
        Calculate startup_order based on dependencies.
        
        Args:
            depends_on: List of service names this service depends on
            
        Returns:
            Calculated startup_order (max of dependencies + 1)
        """        
        try:
            config = DeploymentConfigurer(self.project_name)
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
    
    def add_service(
        self,
        service_name: str,
        depends_on: Optional[List[str]] = None,
        server_zone: str = "lon1",
        servers_count: int = 1,
        dockerfile: Optional[str] = None,
        dockerfile_content: Optional[Dict[str, str]] = None,
        image: Optional[str] = None,
        build_context: Optional[str] = None,
        git_repo: Optional[str] = None,
        git_token: Optional[str] = None,
        auto_scaling: Optional[Union[bool, Dict[str, Any]]] = None,
        **other_config
    ) -> 'ProjectDeployer':
        """
        Add generic service to project (fluent API).
        
        Args:
            service_name: Service name
            depends_on: List of service names this service depends on
                Automatically calculates startup_order to be higher than dependencies
            server_zone: DigitalOcean zone
            servers_count: Number of servers/replicas
            dockerfile: Path to Dockerfile
            dockerfile_content: Inline Dockerfile
            image: Pre-built image
            build_context: Build context path
            git_repo: Git repository URL
            git_token: Personal access token for private Git repositories
            auto_scaling: Enable auto-scaling
            **other_config: Additional config (can include 'startup_order' to override)
            
        Returns:
            Self for chaining
        """
        # Calculate startup_order from dependencies or use explicit override
        if 'startup_order' in other_config:
            startup_order = other_config.pop('startup_order')
        elif depends_on:
            startup_order = self._calculate_startup_order(depends_on)
            other_config['depends_on'] = depends_on
        else:
            startup_order = 1  # Default for generic services
        
        if git_repo:
            other_config['git_repo'] = git_repo
        if git_token:            
            other_config['git_token'] = Encryption.encode(git_token)

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
    
    def cleanup_git_checkouts(self) -> None:
        """
        Clean up all git checkouts for this project.
        
        Example:
            project.cleanup_git_checkouts()
        """        
        GitManager.cleanup_checkouts(self.project_name)

    # =========================================================================
    # SERVICE MANAGEMENT - Convenience Methods
    # =========================================================================
    
    def add_python_service(
        self,
        service_name: str,
        python_version: str = "3.11",
        depends_on: Optional[List[str]] = None,
        server_zone: str = "lon1",
        servers_count: int = 1,
        requirements_files: Optional[List[str]] = None,
        command: Optional[str] = None,
        port: Optional[int] = None,
        build_context: Optional[str] = None,
        git_repo: Optional[str] = None,
        git_token: Optional[str] = None,
        auto_scaling: Optional[Union[bool, Dict[str, Any]]] = None,
        **other_config
    ) -> 'ProjectDeployer':
        """
        Add Python service with automatic Dockerfile generation (fluent API).
        
        Automatically generates optimized Dockerfile for Python applications.
        You can override by providing dockerfile or dockerfile_content.
        
        Args:
            service_name: Service name
            python_version: Python version (default: "3.11")
            depends_on: List of service names this service depends on
            server_zone: DigitalOcean zone
            servers_count: Number of servers/replicas
            requirements_files: List of requirements files to install (default: ["requirements.txt"])
                **IMPORTANT: These files must exist in your build_context or git_repo**
                Example: ["requirements.txt", "requirements-prod.txt"]
                Paths are relative to build context root
            command: Command to run (default: "python {service_name}.py")
                Example: "python app.py", "gunicorn app:app", "uvicorn main:app --host 0.0.0.0"
            port: Port to expose (optional, for web services)
            build_context: Build context path (must contain requirements files)
            git_repo: Git repository URL with optional ref (must contain requirements files)
            git_token: Personal access token for private Git repositories
            auto_scaling: Enable auto-scaling
            **other_config: Additional config (env_vars, volumes, domain, etc.)
                Can include 'dockerfile_content' to override auto-generation
            
        Returns:
            Self for chaining
            
        Example:
            # Simple API with requirements.txt in repo root
            project.add_python_service(
                "api",
                requirements_files=["requirements.txt"],
                command="python api.py",
                port=8000,
                build_context="/path/to/code",  # Must contain requirements.txt
                servers_count=3
            )
            
            # Multiple requirements files
            project.add_python_service(
                "api",
                requirements_files=["requirements.txt", "requirements-prod.txt"],
                command="gunicorn app:app --bind 0.0.0.0:8000",
                port=8000,
                build_context="/path/to/code",  # Must contain both files
                servers_count=3
            )
            
            # From Git repo (requirements.txt must be in repo)
            project.add_python_service(
                "api",
                git_repo="https://github.com/user/myapp.git@main",
                requirements_files=["requirements.txt"],
                command="python api.py",
                port=8000,
                servers_count=3
            )
            
            # Requirements in subdirectory
            project.add_python_service(
                "api",
                requirements_files=["docker/requirements.txt"],  # Relative to build context
                command="python api.py",
                port=8000,
                build_context="/path/to/code",
                servers_count=3
            )
            
            # Worker service (no port, no requirements files needed)
            project.add_python_service(
                "worker",
                requirements_files=[],  # Empty list = no pip install
                command="python worker.py",
                build_context="/path/to/code",
                servers_count=2
            )            
        
        Dockerfile Structure:
            The generated Dockerfile will:
            1. FROM python:{version}-slim
            2. WORKDIR /app
            3. COPY each requirements file
            4. RUN pip install for each requirements file
            5. COPY . .
            6. EXPOSE {port} (if specified)
            7. CMD {command}
            
        File Requirements:
            - All requirements_files must exist in build_context or git_repo
            - Paths are relative to the root of build_context/git_repo
            - If files don't exist, Docker build will fail with clear error
        """
        # If user provided custom dockerfile or dockerfile_content, use regular add_service
        if 'dockerfile' in other_config or 'dockerfile_content' in other_config:
            return self.add_service(
                service_name,
                depends_on=depends_on,
                server_zone=server_zone,
                servers_count=servers_count,
                build_context=build_context,
                git_repo=git_repo,
                git_token=git_token,
                auto_scaling=auto_scaling,
                **other_config
            )
        
        # Generate Dockerfile content
        dockerfile_lines = {}
        line_num = 1
        
        # Base image
        dockerfile_lines[str(line_num)] = f"FROM python:{python_version}-slim"
        line_num += 1
        
        # Working directory
        dockerfile_lines[str(line_num)] = "WORKDIR /app"
        line_num += 1
        
        # Copy and install requirements
        if not requirements_files:
            requirements_files = ["requirements.txt"]
        
        for req_file in requirements_files:
            dockerfile_lines[str(line_num)] = f"COPY {req_file} ."
            line_num += 1
        
        for req_file in requirements_files:
            dockerfile_lines[str(line_num)] = f"RUN pip install --no-cache-dir -r {req_file}"
            line_num += 1
        
        # Copy application code
        dockerfile_lines[str(line_num)] = "COPY . ."
        line_num += 1
        
        # Expose port if specified
        if port:
            dockerfile_lines[str(line_num)] = f"EXPOSE {port}"
            line_num += 1
        
        # Command
        if not command:
            command = f"python {service_name}.py"
        
        # Parse command into CMD format
        cmd_parts = command.split()
        cmd_json = '["' + '", "'.join(cmd_parts) + '"]'
        dockerfile_lines[str(line_num)] = f"CMD {cmd_json}"
        
        # Add generated dockerfile_content to other_config
        other_config['dockerfile_content'] = dockerfile_lines
        
        # Call regular add_service with generated Dockerfile
        return self.add_service(
            service_name,
            depends_on=depends_on,
            server_zone=server_zone,
            servers_count=servers_count,
            build_context=build_context,
            git_repo=git_repo,
            git_token=git_token,
            auto_scaling=auto_scaling,
            **other_config
        )

    def add_nodejs_service(
        self,
        service_name: str,
        node_version: str = "20",
        depends_on: Optional[List[str]] = None,
        server_zone: str = "lon1",
        servers_count: int = 1,
        package_manager: str = "npm",
        install_command: Optional[str] = None,
        build_command: Optional[str] = None,
        command: Optional[str] = None,
        port: Optional[int] = None,
        build_context: Optional[str] = None,
        git_repo: Optional[str] = None,
        git_token: Optional[str] = None,
        auto_scaling: Optional[Union[bool, Dict[str, Any]]] = None,
        **other_config
    ) -> 'ProjectDeployer':
        """
        Add Node.js service with automatic Dockerfile generation (fluent API).
        
        Automatically generates optimized Dockerfile for Node.js applications.
        You can override by providing dockerfile or dockerfile_content.
        
        Args:
            service_name: Service name
            node_version: Node.js version (default: "20")
            depends_on: List of service names this service depends on
            server_zone: DigitalOcean zone
            servers_count: Number of servers/replicas
            package_manager: Package manager - "npm", "yarn", or "pnpm" (default: "npm")
            install_command: Custom install command (default: based on package_manager)
                - npm: "npm ci --only=production"
                - yarn: "yarn install --frozen-lockfile --production"
                - pnpm: "pnpm install --frozen-lockfile --prod"
            build_command: Build command if needed (e.g., "npm run build", "yarn build")
            command: Command to run (default: "npm start")
                Example: "node app.js", "npm start", "yarn start", "node dist/main.js"
            port: Port to expose (optional, for web services)
            build_context: Build context path (or use git_repo)
            git_repo: Git repository URL with optional ref
            git_token: Personal access token for private Git repositories
            auto_scaling: Enable auto-scaling
            **other_config: Additional config (env_vars, volumes, domain, etc.)
                Can include 'dockerfile_content' to override auto-generation
            
        Returns:
            Self for chaining

        File Requirements:
            **IMPORTANT: Your build_context or git_repo must contain:**
            - package.json (required)
            - package-lock.json (for npm)
            - yarn.lock (for yarn)
            - pnpm-lock.yaml (for pnpm)
            
            If these files don't exist, Docker build will fail.
            
        Example:
            # Express API (must have package.json in /path/to/code)
            project.add_nodejs_service(
                "api",
                command="node app.js",
                port=3000,
                build_context="/path/to/code",
                servers_count=3
            
            # Next.js app with build step
            project.add_nodejs_service(
                "web",
                build_command="npm run build",
                command="npm start",
                port=3000,
                servers_count=3
            )
            
            # TypeScript project
            project.add_nodejs_service(
                "api",
                build_command="npm run build",
                command="node dist/main.js",
                port=3000,
                servers_count=3
            )
            
            # From Git with yarn
            project.add_nodejs_service(
                "api",
                git_repo="https://github.com/user/myapp.git@main",
                package_manager="yarn",
                command="yarn start",
                port=3000,
                servers_count=3
            )
            
            # Worker (no port)
            project.add_nodejs_service(
                "worker",
                command="node worker.js",
                servers_count=2
            )
        """
        # If user provided custom dockerfile or dockerfile_content, use regular add_service
        if 'dockerfile' in other_config or 'dockerfile_content' in other_config:
            return self.add_service(
                service_name,
                depends_on=depends_on,
                server_zone=server_zone,
                servers_count=servers_count,
                build_context=build_context,
                git_repo=git_repo,
                git_token=git_token,
                auto_scaling=auto_scaling,
                **other_config
            )
        
        # Determine install command based on package manager
        if not install_command:
            if package_manager == "yarn":
                install_command = "yarn install --frozen-lockfile --production"
            elif package_manager == "pnpm":
                install_command = "pnpm install --frozen-lockfile --prod"
            else:  # npm
                install_command = "npm ci --only=production"
        
        # Default command
        if not command:
            if package_manager == "yarn":
                command = "yarn start"
            elif package_manager == "pnpm":
                command = "pnpm start"
            else:
                command = "npm start"
        
        # Generate Dockerfile content
        dockerfile_lines = {}
        line_num = 1
        
        # Base image
        dockerfile_lines[str(line_num)] = f"FROM node:{node_version}-alpine"
        line_num += 1
        
        # Working directory
        dockerfile_lines[str(line_num)] = "WORKDIR /app"
        line_num += 1
        
        # Copy package files
        if package_manager == "yarn":
            dockerfile_lines[str(line_num)] = "COPY package.json yarn.lock ./"
        elif package_manager == "pnpm":
            dockerfile_lines[str(line_num)] = "COPY package.json pnpm-lock.yaml ./"
        else:  # npm
            dockerfile_lines[str(line_num)] = "COPY package*.json ./"
        line_num += 1
        
        # Install dependencies
        dockerfile_lines[str(line_num)] = f"RUN {install_command}"
        line_num += 1
        
        # Copy application code
        dockerfile_lines[str(line_num)] = "COPY . ."
        line_num += 1
        
        # Build step if specified
        if build_command:
            dockerfile_lines[str(line_num)] = f"RUN {build_command}"
            line_num += 1
        
        # Expose port if specified
        if port:
            dockerfile_lines[str(line_num)] = f"EXPOSE {port}"
            line_num += 1
        
        # Command
        cmd_parts = command.split()
        cmd_json = '["' + '", "'.join(cmd_parts) + '"]'
        dockerfile_lines[str(line_num)] = f"CMD {cmd_json}"
        
        # Add generated dockerfile_content to other_config
        other_config['dockerfile_content'] = dockerfile_lines
        
        # Call regular add_service with generated Dockerfile
        return self.add_service(
            service_name,
            depends_on=depends_on,
            server_zone=server_zone,
            servers_count=servers_count,
            build_context=build_context,
            git_repo=git_repo,
            git_token=git_token,
            auto_scaling=auto_scaling,
            **other_config
        )

    def add_react_service(
        self,
        service_name: str = "web",
        node_version: str = "20",
        depends_on: Optional[List[str]] = None,
        server_zone: str = "lon1",
        servers_count: int = 1,
        package_manager: str = "npm",
        build_command: str = "npm run build",
        build_dir: str = "build",
        nginx_config: Optional[str] = None,
        port: int = 80,
        build_context: Optional[str] = None,
        git_repo: Optional[str] = None,
        git_token: Optional[str] = None,
        auto_scaling: Optional[Union[bool, Dict[str, Any]]] = None,
        **other_config
    ) -> 'ProjectDeployer':
        """
        Add React/Vue/Angular/Svelte static website with Nginx (fluent API).
        
        Automatically generates optimized multi-stage Dockerfile that:
        1. Builds the app with Node.js
        2. Serves static files with Nginx
        
        You can override by providing dockerfile or dockerfile_content.
        
        Args:
            service_name: Service name (default: "web")
            node_version: Node.js version for build stage (default: "20")
            depends_on: List of service names this service depends on
            server_zone: DigitalOcean zone
            servers_count: Number of servers/replicas
            package_manager: Package manager - "npm", "yarn", or "pnpm" (default: "npm")
            build_command: Build command (default: "npm run build")
                - React: "npm run build"
                - Vue: "npm run build"
                - Angular: "npm run build"
                - Svelte: "npm run build"
                - Next.js (static export): "npm run build && npm run export"
            build_dir: Output directory name (default: "build")
                - React (CRA): "build"
                - Vue: "dist"
                - Angular: "dist/{project_name}"
                - Svelte: "public/build" or "build"
                - Next.js: "out"
            nginx_config: Custom nginx.conf content (optional)
                If not provided, uses default SPA config with history fallback
            port: Port to expose (default: 80)
            build_context: Build context path (or use git_repo)
            git_repo: Git repository URL with optional ref
            git_token: Personal access token for private Git repositories
            auto_scaling: Enable auto-scaling
            **other_config: Additional config (env_vars, volumes, domain, etc.)
                Can include 'dockerfile_content' to override auto-generation
            
        Returns:
            Self for chaining
            
        File Requirements:
            **IMPORTANT: Your build_context or git_repo must contain:**
            - package.json with build script (required)
            - package-lock.json / yarn.lock / pnpm-lock.yaml (required)
            - Source code (src/, public/, etc.)
            - The build_command must output to the specified build_dir
            
            If these don't exist or build fails, Docker build will fail.
            
        SSL & Domain:
            - Use `domain="www.example.com"` parameter
            - SSL is handled by nginx sidecar (service mesh layer)
            - This container serves HTTP on port 80
            - Nginx sidecar handles HTTPS (443) with Let's Encrypt certificates
            
        Example:
            # React app (must have package.json with "build" script)
            project.add_react_service(
                "web",
                build_context="/path/to/react-app",
                domain="www.example.com",  # SSL handled by nginx sidecar
                servers_count=3
            )

            # Vue app
            project.add_react_service(
                "web",
                build_dir="dist",
                build_context="/path/to/vue-app",
                domain="www.example.com"
            )
            
            # Angular app
            project.add_react_service(
                "web",
                build_dir="dist/myapp",
                build_context="/path/to/angular-app",
                domain="www.example.com"
            )
            
            # From Git repo
            project.add_react_service(
                "web",
                git_repo="https://github.com/user/myapp.git@main",
                domain="www.example.com",
                servers_count=3
            )
            
            # With custom nginx config
            project.add_react_service(
                "web",
                nginx_config='''
                    server {
                        listen 80;
                        location / {
                            root /usr/share/nginx/html;
                            try_files $uri $uri/ /index.html;
                        }
                        location /api {
                            proxy_pass http://api:3000;
                        }
                    }
                ''',
                build_context="/path/to/react-app"
            )
        """
        # If user provided custom dockerfile or dockerfile_content, use regular add_service
        if 'dockerfile' in other_config or 'dockerfile_content' in other_config:
            return self.add_service(
                service_name,
                depends_on=depends_on,
                server_zone=server_zone,
                servers_count=servers_count,
                build_context=build_context,
                git_repo=git_repo,
                git_token=git_token,
                auto_scaling=auto_scaling,
                **other_config
            )
        
        # Determine install command based on package manager
        if package_manager == "yarn":
            install_command = "yarn install --frozen-lockfile"
        elif package_manager == "pnpm":
            install_command = "pnpm install --frozen-lockfile"
        else:  # npm
            install_command = "npm ci"
        
        # Default nginx config for SPAs
        if not nginx_config:
            nginx_config = """server {
        listen 80;
        server_name _;
        
        root /usr/share/nginx/html;
        index index.html;
        
        # Gzip compression
        gzip on;
        gzip_types text/plain text/css application/json application/javascript text/xml application/xml application/xml+rss text/javascript;
        
        # SPA routing - fallback to index.html
        location / {
            try_files $uri $uri/ /index.html;
        }
        
        # Cache static assets
        location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {
            expires 1y;
            add_header Cache-Control "public, immutable";
        }
        
        # Security headers
        add_header X-Frame-Options "SAMEORIGIN" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-XSS-Protection "1; mode=block" always;
    }"""
        
        # Generate Dockerfile content (multi-stage build)
        dockerfile_lines = {}
        line_num = 1
        
        # Stage 1: Build
        dockerfile_lines[str(line_num)] = f"FROM node:{node_version}-alpine AS builder"
        line_num += 1
        
        dockerfile_lines[str(line_num)] = "WORKDIR /app"
        line_num += 1
        
        # Copy package files
        if package_manager == "yarn":
            dockerfile_lines[str(line_num)] = "COPY package.json yarn.lock ./"
        elif package_manager == "pnpm":
            dockerfile_lines[str(line_num)] = "COPY package.json pnpm-lock.yaml ./"
        else:  # npm
            dockerfile_lines[str(line_num)] = "COPY package*.json ./"
        line_num += 1
        
        # Install dependencies
        dockerfile_lines[str(line_num)] = f"RUN {install_command}"
        line_num += 1
        
        # Copy source and build
        dockerfile_lines[str(line_num)] = "COPY . ."
        line_num += 1
        
        dockerfile_lines[str(line_num)] = f"RUN {build_command}"
        line_num += 1
        
        # Stage 2: Production (Nginx)
        dockerfile_lines[str(line_num)] = "FROM nginx:alpine"
        line_num += 1
        
        # Copy built files
        dockerfile_lines[str(line_num)] = f"COPY --from=builder /app/{build_dir} /usr/share/nginx/html"
        line_num += 1
        
        # Copy custom nginx config
        dockerfile_lines[str(line_num)] = "RUN rm /etc/nginx/conf.d/default.conf"
        line_num += 1
        
        # Create nginx config file inline
        dockerfile_lines[str(line_num)] = f"RUN echo '{nginx_config}' > /etc/nginx/conf.d/default.conf"
        line_num += 1
        
        # Expose port
        dockerfile_lines[str(line_num)] = f"EXPOSE {port}"
        line_num += 1
        
        # Start nginx
        dockerfile_lines[str(line_num)] = 'CMD ["nginx", "-g", "daemon off;"]'
        
        # Add generated dockerfile_content to other_config
        other_config['dockerfile_content'] = dockerfile_lines
        
        # Call regular add_service with generated Dockerfile
        return self.add_service(
            service_name,
            depends_on=depends_on,
            server_zone=server_zone,
            servers_count=servers_count,
            build_context=build_context,
            git_repo=git_repo,
            git_token=git_token,
            auto_scaling=auto_scaling,
            **other_config
        )

    def add_postgres(
        self,
        version: str = "15",
        server_zone: str = "lon1",
        servers_count: int = 1,
        depends_on: Optional[List[str]] = None,
        **other_config
    ) -> 'ProjectDeployer':
        """
        Add PostgreSQL database service (fluent API).
        
        Args:
            version: PostgreSQL version (default: "15")
            server_zone: DigitalOcean zone
            servers_count: Number of replicas
            depends_on: List of service names this service depends on
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
            depends_on,
            **other_config
        )
        return self
    
    def add_redis(
        self,
        version: str = "7-alpine",
        server_zone: str = "lon1",
        servers_count: int = 1,
        depends_on: Optional[List[str]] = None,
        **other_config
    ) -> 'ProjectDeployer':
        """
        Add Redis cache service (fluent API).
        
        Args:
            version: Redis version (default: "7-alpine")
            server_zone: DigitalOcean zone
            servers_count: Number of replicas
            depends_on: List of service names this service depends on
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
            depends_on,
            **other_config
        )
        return self
    
    def add_opensearch(
        self,
        version: str = "2",
        server_zone: str = "lon1",
        servers_count: int = 1,
        depends_on: Optional[List[str]] = None,
        **other_config
    ) -> 'ProjectDeployer':
        """
        Add OpenSearch service (fluent API).
        
        Args:
            version: OpenSearch version (default: "2")
            server_zone: DigitalOcean zone
            servers_count: Number of replicas
            depends_on: List of service names this service depends on
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
            depends_on,
            **other_config
        )
        return self
    
    def add_nginx(
        self,
        version: str = "alpine",
        server_zone: str = "lon1",
        servers_count: int = 1,
        depends_on: Optional[List[str]] = None,
        **other_config
    ) -> 'ProjectDeployer':
        """
        Add Nginx web server service (fluent API).
        
        Args:
            version: Nginx version (default: "alpine")
            server_zone: DigitalOcean zone
            servers_count: Number of replicas
            depends_on: List of service names this service depends on
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
            depends_on,
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

    # ========================================
    # FILE OPERATIONS (delegate to Deployer)
    # ========================================
    
    def push_config(self, env: str, targets: List[str] = None) -> bool:
        """Push config/secrets/files to servers"""        
        deployer = Deployer(self.project_name)
        return deployer.push_config(env, targets)
    
    def pull_data(self, env: str, targets: List[str] = None) -> bool:
        """Pull data/logs/backups from servers"""       
        deployer = Deployer(self.project_name)
        return deployer.pull_data(env, targets)
    
    def pull_backups(self, env: str, service: str = None) -> bool:
        """Pull backups from servers"""        
        deployer = Deployer(self.project_name)
        return deployer.pull_backups(env, service)
    
    def sync_files(self, env: str) -> bool:
        """Full bidirectional sync"""        
        deployer = Deployer(self.project_name)
        return deployer.full_sync(env)
    
    # ========================================
    # SECRETS MANAGEMENT (delegate to SecretsRotator)
    # ========================================
    
    def rotate_secrets(
        self, 
        env: str, 
        services: List[str] = None,
        auto_deploy: bool = False
    ) -> bool:
        """
        Rotate passwords for stateful services.
        
        Args:
            env: Environment name
            services: List of services to rotate (None = all)
            auto_deploy: If True, automatically redeploy after rotation
            
        Returns:
            True if successful
        """        
        rotator = SecretsRotator(self.project_name, env)
        
        if services:
            for service in services:
                rotator.rotate_service_password(service)
        else:
            rotator.rotate_all_secrets()
        
        if auto_deploy:
            return self.deploy(env=env)
        
        return True
    
    def list_secrets(self, env: str) -> Dict[str, List[str]]:
        """List all secrets for an environment"""       
        rotator = SecretsRotator(self.project_name, env)
        return rotator.list_secrets()
    
    # ========================================
    # HEALTH MONITORING (delegate to HealthMonitor)
    # ========================================
    
    def check_health(self) -> None:
        """Run health check once (monitor_and_heal)"""        
        HealthMonitor.monitor_and_heal()
    
    def get_health_status(self) -> Dict[str, Any]:
        """
        Get current health status of all servers.
        
        Returns:
            Dict with servers categorized by health
        """
        all_servers = ServerInventory.get_servers(
            deployment_status=ServerInventory.STATUS_ACTIVE
        )
        
        healthy = []
        unhealthy = []
        
        for server in all_servers:
            if HealthMonitor.is_server_healthy(server):
                healthy.append(server)
            else:
                unhealthy.append(server)
        
        return {
            'healthy': healthy,
            'unhealthy': unhealthy,
            'total': len(all_servers)
        }
    
    # ========================================
    # SERVER MANAGEMENT (delegate to ServerInventory)
    # ========================================
    
    def list_servers(
        self, 
        env: str = None, 
        zone: str = None,
        status: str = None
    ) -> List[Dict[str, Any]]:
        """
        List servers with optional filtering.
        
        Args:
            env: Filter by environment (e.g., "prod")
            zone: Filter by zone (e.g., "lon1")
            status: Filter by status ("active", "blue", "destroying")
            
        Returns:
            List of server dicts
        """        
        
        # Get all servers or filtered by status
        if status:
            servers = ServerInventory.get_servers(deployment_status=status)
        else:
            servers = ServerInventory.list_all_servers()
        
        # Filter by zone if specified
        if zone:
            servers = [s for s in servers if s.get('zone') == zone]
        
        # Filter by env if specified
        if env:
            # Get servers that have containers for this env            
            env_servers = set()
            
            services = self.deployment_configurer.get_services(env)
            for service_name in services:
                service_servers = LiveDeploymentQuery.get_servers_running_service(
                    self.project_name, env, service_name
                )
                env_servers.update(service_servers)
            
            servers = [s for s in servers if s['ip'] in env_servers]
        
        return servers
    
    def destroy_server(self, server_ip: str) -> bool:
        """
        Destroy a specific server.
        
        Args:
            server_ip: IP address of server to destroy
            
        Returns:
            True i successful
        """        
        # Find server
        servers = ServerInventory.list_all_servers()
        server = next((s for s in servers if s['ip'] == server_ip), None)
        
        if not server:
            log(f"Server {server_ip} not found")
            return False
        
        # Destroy via DO API
        DOManager.destroy_droplet(server['droplet_id'])
        
        # Release from inventory
        ServerInventory.release_servers([server_ip], destroy=False)
        
        return True
    
    # ========================================
    # DEPLOYMENT STATE (delegate to DeploymentStateManager)
    # ========================================
    
    def get_deployment_state(
        self, 
        env: str, 
        service: str = None
    ) -> Dict[str, Any]:
        """
        Get current deployment state.
        
        Args:
            env: Environment name
            service: Optional service name (None = all services)
            
        Returns:
            Deployment state dict
        """       
        if service:
            return DeploymentStateManager.get_current_deployment(
                self.project_name, env, service
            )
        else:
            # Get all services
            services = self.deployment_configurer.get_services(env)
            return {
                service_name: DeploymentStateManager.get_current_deployment(
                    self.project_name, env, service_name
                )
                for service_name in services
            }
    
    def get_deployment_history(
        self, 
        env: str, 
        service: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get deployment history for a service"""        
        return DeploymentStateManager.get_deployment_history(
            self.project_name, env, service, limit
        )

    # =========================================================================
    # UTILITY
    # =========================================================================
    
    def __repr__(self) -> str:
        return f"ProjectDeployer('{self.project_name}')"