import hashlib
import re
from typing import List, Dict, Optional
from secrets_manager import SecretsManager
from services_config import ServiceConfig

from enums import Envs, ServiceTypes

class HealthConfig:
    def __init__(self):
        self.interval_minutes = 5
        self.check_interval_seconds = 30
        self.failure_timeout_minutes = 3
        self.health_timeout_seconds = 10

class ContainerGenerator:
    """
    A utility class for generating container configuration files (Dockerfiles) for different service types.
    
    Provides static methods to generate optimized, production-ready Dockerfiles for web services,
    workers, databases, and infrastructure components with consistent naming, port allocation,
    and health check configurations.
    
    Key Features:
    - Deterministic port assignment based on project/environment/service combination
    - Human-readable identifier generation for database names, users, clusters
    - Service-specific optimizations (FastAPI for web, process monitoring for workers)
    - Configurable health checks with custom intervals and timeouts
    - Support for multiple database and infrastructure service types
    
    Supported Service Types:
    - WEB: FastAPI applications with uvicorn
    - WORKER: Background Python workers with process monitoring
    - POSTGRES: PostgreSQL databases with custom ports and identifiers
    - REDIS: Redis cache servers with custom configurations
    - OPENSEARCH: OpenSearch clusters with security disabled for development
    - NGINX: Load balancers and reverse proxies
    
    Example Usage:
        ```python
        # Generate a web service Dockerfile
        dockerfile = ContainerGenerator.generate_container_file_content(
            ServiceTypes.WEB, "ecommerce", Envs.PROD, "api"
        )
        
        # Generate with custom health config
        health_config = HealthConfig()
        health_config.check_interval_seconds = 60
        dockerfile = ContainerGenerator.generate_container_file_content(
            ServiceTypes.POSTGRES, "ecommerce", Envs.PROD, "maindb", health_config
        )
        ```
    
    Design Principles:
    - All methods are static (no instance state required)
    - Deterministic output for consistent deployments
    - Security-focused (non-root users, minimal base images)
    - Production-ready configurations with proper health checks
    """

    @staticmethod
    def hash_port(service_type: ServiceTypes, project_name: str, env: Envs) -> Optional[int]:
        """
        Generate a deterministic port number based on service type, project, and environment.
        
        Creates consistent port assignments across deployments to avoid conflicts while
        ensuring different projects and environments get different port ranges.
        
        Args:
            service_type: Type of service (WEB, POSTGRES, REDIS, etc.)
            project_name: Name of the project
            env: Environment (DEV, TEST, UAT, PROD)
            
        Returns:
            int: Deterministic port number within service-specific range, or None for WORKER services
            
        Port Ranges:
            - WEB: 8000-9999
            - POSTGRES: 5000-5999  
            - REDIS: 6000-6999
            - OPENSEARCH: 9000-9999
            - NGINX: 7000-7999
            - WORKER: None (no exposed ports)
            
        Examples:
            ```python
            port = ContainerGenerator.hash_port(ServiceTypes.WEB, "ecommerce", Envs.PROD)
            # Returns: 8234 (example, always same for these inputs)
            
            port = ContainerGenerator.hash_port(ServiceTypes.WORKER, "ecommerce", Envs.PROD)  
            # Returns: None
            ```
        """
        env = Envs.to_enum(env)
        if service_type == ServiceTypes.WORKER:
            return None
        
        # Generate deterministic port based on project, env, and service type
        hash_input = f"{project_name}-{env.value}-{service_type.value}".encode()
        hash_value = int(hashlib.md5(hash_input).hexdigest()[:8], 16)
        
        # Different port ranges for different service types
        if service_type == ServiceTypes.WEB:
            return 8000 + (hash_value % 2000)  # 8000-9999
        elif service_type == ServiceTypes.POSTGRES:
            return 5000 + (hash_value % 1000)  # 5000-5999
        elif service_type == ServiceTypes.REDIS:
            return 6000 + (hash_value % 1000)  # 6000-6999
        elif service_type == ServiceTypes.OPENSEARCH:
            return 9000 + (hash_value % 1000)  # 9000-9999
        elif service_type == ServiceTypes.NGINX:
            return 7000 + (hash_value % 1000)  # 7000-7999
        else:
            return 8000 + (hash_value % 2000)  # Default range

    @staticmethod
    def generate_identifier(project_name: str, env: Envs, identifier_type: str, max_length: int = 60) -> str:
        """
        Generate human-readable identifiers for database names, users, clusters, etc.
        
        Creates consistent, readable identifiers while sanitizing project names and
        respecting length constraints for different systems.
        
        Args:
            project_name: Name of the project (will be sanitized)
            env: Environment (DEV, TEST, UAT, PROD)
            identifier_type: Type of identifier ("database", "user", "cluster", etc.)
            max_length: Maximum length of resulting identifier (default: 60)
            
        Returns:
            str: Sanitized identifier in format "{project}_{env}_{type}"
            
        Examples:
            ```python
            db_name = ContainerGenerator.generate_identifier("e-commerce", Envs.PROD, "database")
            # Returns: "ecommerce_prod_database"
            
            user = ContainerGenerator.generate_identifier("My App!", Envs.DEV, "user")
            # Returns: "myapp_dev_user"
            ```
            
        Notes:
            - Removes special characters and converts to lowercase
            - Truncates project name if total length exceeds max_length
            - Ensures consistent naming across deployments
        """
        env = Envs.to_enum(env)
        # Sanitize project name (remove special chars, lowercase)
        clean_project = re.sub(r'[^a-zA-Z0-9]', '', project_name).lower()
        identifier = f"{clean_project}_{env.value}_{identifier_type}"
        
        if len(identifier) > max_length:
            # Truncate project name if needed
            max_project_len = max_length - len(f"_{env.value}_{identifier_type}")
            clean_project = clean_project[:max_project_len]
            identifier = f"{clean_project}_{env.value}_{identifier_type}"
        
        return identifier

    @staticmethod
    def generate_network_name(project_name: str, env: Envs) -> str:
        '''Generate the network name for a given project/env - can be used for creating an internal network among containers in a given server.'''
        env = Envs.to_enum(env)
        return f"{project_name}_{env.value}_network"

    @staticmethod
    def generate_container_file_content(service_type: ServiceTypes, project_name: str, env: Envs, service_name: str, 
                                        health_config: HealthConfig = None, service_config: ServiceConfig = None) -> str:
        """
        Generate complete Dockerfile content for the specified service type.
        
        Creates production-ready Dockerfiles with service-specific optimizations,
        security configurations, health checks, and custom package installations.
        Automatically calculates ports and identifiers for consistent deployments.
        
        Args:
            service_type: Type of service to generate Dockerfile for
            project_name: Name of the project (used for port calculation and identifiers)
            env: Environment (DEV, TEST, UAT, PROD)
            service_name: Name of the specific service instance
            health_config: Custom health check configuration (optional)
            service_config: Custom service configuration for packages, commands, etc. (optional)
            
        Returns:
            str: Complete Dockerfile content ready for docker build
            
        Raises:
            ValueError: If service_type is not supported
            
        Examples:
            ```python
            # Standard web service
            dockerfile = ContainerGenerator.generate_container_file_content(
                ServiceTypes.WEB, "ecommerce", Envs.PROD, "api"
            )
            
            # Image processing API with custom packages
            config = ServiceConfig(
                packages=["imagemagick", "ffmpeg"],
                environment_vars={"MAX_IMAGE_SIZE": "50MB"}
            )
            dockerfile = ContainerGenerator.generate_container_file_content(
                ServiceTypes.WEB, "ecommerce", Envs.PROD, "image_api", 
                service_config=config
            )
            
            # Backup worker with cron
            backup_config = CommonServiceConfigs.backup_worker("ecommerce", "prod")
            dockerfile = ContainerGenerator.generate_container_file_content(
                ServiceTypes.WORKER, "ecommerce", Envs.PROD, "backup_worker",
                service_config=backup_config
            )
            ```
            
        Service-Specific Features:
            - WEB: FastAPI with uvicorn, health endpoint checks, calculated ports
            - WORKER: Process monitoring, no exposed ports, Python script execution
            - POSTGRES: Custom ports, human-readable database/user names
            - REDIS: Custom ports, optimized Redis configuration
            - OPENSEARCH: Cluster configuration, security disabled for development
            - NGINX: Load balancer configuration, proxy setup
            
        Security Features:
            - Non-root user execution for all services
            - Minimal base images (slim/alpine variants)
            - Proper file permissions and ownership
            - Health checks for container orchestration
        """   
        env = Envs.to_enum(env)     
        if health_config is None:
            health_config = HealthConfig()
        
        if service_config is None:
            service_config = ServiceConfig()
        
        port = ContainerGenerator.hash_port(service_type, project_name, env)
        
        if service_type == ServiceTypes.WEB:
            return ContainerGenerator._generate_web(service_type, project_name, env, port, service_name, health_config, service_config)
        
        elif service_type == ServiceTypes.WORKER:
            return ContainerGenerator._generate_worker(project_name, env, service_name, health_config, service_config)
        
        elif service_type == ServiceTypes.POSTGRES:
            return ContainerGenerator._generate_postgres(service_name, port, project_name, env, health_config, service_config)
        
        elif service_type == ServiceTypes.REDIS:
            return ContainerGenerator._generate_redis(service_name, port, project_name, env, health_config, service_config)
        
        elif service_type == ServiceTypes.OPENSEARCH:
            return ContainerGenerator._generate_opensearch(service_name, port, project_name, env, health_config, service_config)
        
        elif service_type == ServiceTypes.NGINX:
            return ContainerGenerator._generate_nginx(service_name, port, project_name, env, health_config, service_config)
        
        else:
            raise ValueError(f"Unknown service type: {service_type}")

    @staticmethod
    def _generate_web(service_type: ServiceTypes, project_name: str, env: Envs, port: int, service_name: str, 
                     health_config: HealthConfig, service_config: ServiceConfig) -> str:
        
        # Build custom sections
        package_install = service_config.get_package_install_command()
        environment_vars = service_config.get_environment_vars()
        setup_commands = service_config.get_setup_commands()
        workdir_command = service_config.get_workdir_command() or "WORKDIR /app"
        user_command = service_config.get_user_command()
        
        # Determine start command
        if service_config.start_command:
            start_cmd = f'CMD {service_config.start_command}'
        else:
            start_cmd = f'CMD ["uvicorn", "{service_name}:app", "--host", "0.0.0.0", "--port", "{port}"]'
        
        dockerfile_parts = [
            "FROM python:3.11-slim",
            "",
            "# Install system dependencies",
            "RUN apt-get update && apt-get install -y \\",
            "    curl \\",
            "    && rm -rf /var/lib/apt/lists/*",
            "",
        ]
        
        # Add custom package installation
        if package_install:
            dockerfile_parts.extend([
                "# Install custom packages",
                package_install,
                "",
            ])
        
        # Add custom environment variables
        if environment_vars:
            dockerfile_parts.extend([
                "# Custom environment variables",
                environment_vars,
                "",
            ])
        
        dockerfile_parts.extend([
            "# Set working directory",
            workdir_command,
            "",
            "# Copy requirements and install Python dependencies",
            "COPY requirements.txt .",
            "RUN pip install --no-cache-dir -r requirements.txt",
            "",
            "# Copy application code",
            "COPY . .",
            "",
        ])
        
        # Add custom setup commands
        if setup_commands:
            dockerfile_parts.extend([
                "# Custom setup commands",
                setup_commands,
                "",
            ])
        
        dockerfile_parts.extend([
            "# Create non-root user",
            "RUN useradd --create-home --shell /bin/bash appuser \\",
            "    && chown -R appuser:appuser /app",
        ])
        
        # Add custom user if specified
        if user_command:
            dockerfile_parts.append(user_command)
        else:
            dockerfile_parts.append("USER appuser")
        
        dockerfile_parts.extend([
            "",
            f"# Health check using HealthConfig values",
            f"HEALTHCHECK --interval={health_config.check_interval_seconds}s --timeout={health_config.health_timeout_seconds}s --start-period=5s --retries=3 \\",
            f"    CMD curl -f http://localhost:{port}/health || exit 1",
            "",
            f"# Expose calculated port",
            f"EXPOSE {port}",
            "",
            f"# Run service",
            start_cmd
        ])
        
        return "\n".join(dockerfile_parts)

    @staticmethod
    def _generate_worker(project_name: str, env: Envs, service_name: str, 
                        health_config: HealthConfig, service_config: ServiceConfig) -> str:
        
        # Build custom sections
        package_install = service_config.get_package_install_command()
        environment_vars = service_config.get_environment_vars()
        setup_commands = service_config.get_setup_commands()
        workdir_command = service_config.get_workdir_command() or "WORKDIR /app"
        user_command = service_config.get_user_command()
        
        # Determine start command
        if service_config.start_command:
            start_cmd = f'CMD {service_config.start_command}'
        else:
            start_cmd = f'CMD ["python", "{service_name}.py"]'
        
        dockerfile_parts = [
            "FROM python:3.11-slim",
            "",
            "# Install system dependencies including ps for health checks",
            "RUN apt-get update && apt-get install -y \\",
            "    procps \\",
            "    && rm -rf /var/lib/apt/lists/*",
            "",
        ]
        
        # Add custom package installation
        if package_install:
            dockerfile_parts.extend([
                "# Install custom packages",
                package_install,
                "",
            ])
        
        # Add custom environment variables
        if environment_vars:
            dockerfile_parts.extend([
                "# Custom environment variables",
                environment_vars,
                "",
            ])
        
        dockerfile_parts.extend([
            "# Set working directory",
            workdir_command,
            "",
            "# Copy requirements and install Python dependencies",
            "COPY requirements.txt .",
            "RUN pip install --no-cache-dir -r requirements.txt",
            "",
            "# Copy application code",
            "COPY . .",
            "",
        ])
        
        # Add custom setup commands
        if setup_commands:
            dockerfile_parts.extend([
                "# Custom setup commands",
                setup_commands,
                "",
            ])
        
        # FIXED: Simplified approach - check the actual user value, not the command
        if service_config.user == "root":
            # Running as root - create appuser but don't switch to it during build
            dockerfile_parts.extend([
                "# Create appuser for potential use by application (but container runs as root)",
                "RUN useradd --create-home --shell /bin/bash appuser \\",
                "    && chown -R appuser:appuser /app",
                "",
                "# Container will run as root - USER command added at end"
            ])
        elif user_command:
            # Custom non-root user specified
            dockerfile_parts.extend([
                "# Create non-root user for ownership",
                "RUN useradd --create-home --shell /bin/bash appuser \\",
                "    && chown -R appuser:appuser /app",
                "",
                user_command
            ])
        else:
            # Default case - create and use appuser
            dockerfile_parts.extend([
                "# Create non-root user",
                "RUN useradd --create-home --shell /bin/bash appuser \\",
                "    && chown -R appuser:appuser /app",
                "",
                "USER appuser"
            ])
        
        # Health check depends on service type
        if service_config.start_command and "cron" in service_config.start_command:
            # Cron-based worker - check if cron is running
            health_check = f"CMD pgrep cron > /dev/null || exit 1"
        else:
            # Regular worker - check if Python process is running
            health_check = f'CMD python -c "import psutil; import sys; worker_found = False; [worker_found := True for proc in psutil.process_iter([\'pid\', \'name\', \'cmdline\']) if \'{service_name}.py\' in \' \'.join(proc.info.get(\'cmdline\', []) or []) or \'{service_name}\' in \' \'.join(proc.info.get(\'cmdline\', []) or [])]; sys.exit(0 if worker_found else 1)"'
        
        dockerfile_parts.extend([
            "",
            f"# Health check using HealthConfig values",
            f"HEALTHCHECK --interval={health_config.check_interval_seconds}s --timeout={health_config.health_timeout_seconds}s --start-period=10s --retries=3 \\",
            f"    {health_check}",
            "",
        ])
        
        # Add user command at the very end if specified
        if user_command:
            dockerfile_parts.append(user_command)
            dockerfile_parts.append("")
        
        dockerfile_parts.extend([
            f"# Run the worker",
            start_cmd
        ])
        
        return "\n".join(dockerfile_parts)

    @staticmethod
    def _generate_postgres(service_name: str, port: int, project_name: str, env: Envs, 
                          health_config: HealthConfig, service_config: ServiceConfig, host='192.168.1.151') -> str:
        db_name = ContainerGenerator.generate_identifier(project_name, env, "database")
        db_user = ContainerGenerator.generate_identifier(project_name, env, "user")
        
        # Build custom sections
        package_install = service_config.get_package_install_command()
        environment_vars = service_config.get_environment_vars()
        setup_commands = service_config.get_setup_commands()
        user_command = service_config.get_user_command()
        
        # FIXED: Proper escaping while maintaining dynamic path
        if service_config.start_command:
            start_cmd = f'CMD {service_config.start_command}'
        else:
            secrets_file = SecretsManager.get_secrets_file()
            start_cmd = f'CMD ["sh", "-c", "POSTGRES_PASSWORD=$(jq -r .postgres {secrets_file}) && export POSTGRES_PASSWORD && docker-entrypoint.sh postgres"]'
        
        dockerfile_parts = [
            "FROM postgres:15-alpine",
            "",
            "# Install jq for JSON parsing",
            "RUN apk add --no-cache jq",
            "",
        ]
        
        # Add custom package installation (Alpine packages)
        if package_install:
            # Convert apt-get to apk for Alpine
            alpine_install = package_install.replace("apt-get update && apt-get install -y", "apk add --no-cache")
            alpine_install = alpine_install.replace("&& rm -rf /var/lib/apt/lists/*", "")
            dockerfile_parts.extend([
                "# Install custom packages",
                alpine_install,
                "",
            ])
        
        dockerfile_parts.extend([
            "# Set deterministic environment variables",
            f"ENV POSTGRES_DB={db_name}",
            f"ENV POSTGRES_USER={db_user}",
            "",
        ])
        
        # Add custom environment variables
        if environment_vars:
            dockerfile_parts.extend([
                "# Custom environment variables",
                environment_vars,
                "",
            ])
        
        dockerfile_parts.extend([
            "# Create custom init directory",
            "RUN mkdir -p /docker-entrypoint-initdb.d",
            "",
            "# QUICK FIX: Create custom pg_hba.conf with your IP address",
            "RUN echo '# Custom pg_hba.conf for development and backup testing' > /tmp/pg_hba.conf && \\",
            "    echo '# TYPE  DATABASE        USER            ADDRESS                 METHOD' >> /tmp/pg_hba.conf && \\",
            "    echo '# \"local\" is for Unix domain socket connections only' >> /tmp/pg_hba.conf && \\",
            "    echo 'local   all             all                                     trust' >> /tmp/pg_hba.conf && \\",
            "    echo '# IPv4 local connections:' >> /tmp/pg_hba.conf && \\",
            "    echo 'host    all             all             127.0.0.1/32            trust' >> /tmp/pg_hba.conf && \\",
            "    echo '# IPv6 local connections:' >> /tmp/pg_hba.conf && \\",
            "    echo 'host    all             all             ::1/128                 trust' >> /tmp/pg_hba.conf && \\",
            "    echo '# Docker network connections:' >> /tmp/pg_hba.conf && \\",
            "    echo 'host    all             all             172.16.0.0/12           trust' >> /tmp/pg_hba.conf && \\",
            f"    echo '# Development machine IP ({host}):' >> /tmp/pg_hba.conf && \\",
            f"    echo 'host    all             all             {host}/32        trust' >> /tmp/pg_hba.conf && \\",
            "    echo '# Allow replication connections' >> /tmp/pg_hba.conf && \\",
            "    echo 'local   replication     all                                     trust' >> /tmp/pg_hba.conf && \\",
            "    echo 'host    replication     all             127.0.0.1/32            trust' >> /tmp/pg_hba.conf && \\",
            "    echo 'host    replication     all             ::1/128                 trust' >> /tmp/pg_hba.conf && \\",
            "    echo 'host    replication     all             172.16.0.0/12           trust' >> /tmp/pg_hba.conf && \\",
            f"    echo 'host    replication     all             {host}/32        trust' >> /tmp/pg_hba.conf",
            "",
            "# Create an init script to copy our custom pg_hba.conf",
            "RUN echo '#!/bin/bash' > /docker-entrypoint-initdb.d/setup-pg-hba.sh && \\",
            "    echo 'echo \"Setting up custom pg_hba.conf for development...\"' >> /docker-entrypoint-initdb.d/setup-pg-hba.sh && \\",
            "    echo 'cp /tmp/pg_hba.conf ${PGDATA}/pg_hba.conf' >> /docker-entrypoint-initdb.d/setup-pg-hba.sh && \\",
            "    echo 'chmod 600 ${PGDATA}/pg_hba.conf' >> /docker-entrypoint-initdb.d/setup-pg-hba.sh && \\",
            "    echo 'chown postgres:postgres ${PGDATA}/pg_hba.conf' >> /docker-entrypoint-initdb.d/setup-pg-hba.sh && \\",
            "    echo 'echo \"Custom pg_hba.conf installed with development IPs\"' >> /docker-entrypoint-initdb.d/setup-pg-hba.sh && \\",
            "    chmod +x /docker-entrypoint-initdb.d/setup-pg-hba.sh",
            "",
        ])
        
        # Add custom setup commands
        if setup_commands:
            dockerfile_parts.extend([
                "# Custom setup commands",
                setup_commands,
                "",
            ])
        
        dockerfile_parts.extend([
            f"# Health check using HealthConfig values",
            f"HEALTHCHECK --interval={health_config.check_interval_seconds}s --timeout={health_config.health_timeout_seconds}s --start-period=30s --retries=3 \\",
            f"    CMD pg_isready -U {db_user} -d {db_name}",
            "",
            "# Expose standard PostgreSQL port",
            "EXPOSE 5432",
            "",
        ])
        
        # Add custom user if specified
        if user_command:
            dockerfile_parts.append(user_command)
            dockerfile_parts.append("")
        
        dockerfile_parts.extend([
            "# Use mounted secrets for password",
            start_cmd
        ])
        
        return "\n".join(dockerfile_parts)

    @staticmethod
    def _generate_redis(service_name: str, port: int, project_name: str, env: Envs, 
                       health_config: HealthConfig, service_config: ServiceConfig) -> str:
        
        # Build custom sections
        package_install = service_config.get_package_install_command()
        environment_vars = service_config.get_environment_vars()
        setup_commands = service_config.get_setup_commands()
        user_command = service_config.get_user_command()
        
        # FIXED: Proper escaping while maintaining dynamic path
        if service_config.start_command:
            start_cmd = f'CMD {service_config.start_command}'
        else:
            secrets_file = SecretsManager.get_secrets_file()
            start_cmd = f'CMD ["sh", "-c", "PASSWORD=$(jq -r .redis {secrets_file}) && redis-server --requirepass $PASSWORD"]'
        
        dockerfile_parts = [
            "FROM redis:7-alpine",
            "",
            "# Install jq for JSON parsing",
            "RUN apk add --no-cache jq",
            "",
        ]
        
        # Add custom package installation (Alpine packages)
        if package_install:
            # Convert apt-get to apk for Alpine
            alpine_install = package_install.replace("apt-get update && apt-get install -y", "apk add --no-cache")
            alpine_install = alpine_install.replace("&& rm -rf /var/lib/apt/lists/*", "")
            dockerfile_parts.extend([
                "# Install custom packages",
                alpine_install,
                "",
            ])
        
        # Add custom environment variables
        if environment_vars:
            dockerfile_parts.extend([
                "# Custom environment variables",
                environment_vars,
                "",
            ])
        
        # Add custom setup commands
        if setup_commands:
            dockerfile_parts.extend([
                "# Custom setup commands",
                setup_commands,
                "",
            ])
        
        dockerfile_parts.extend([
            f"# Health check using HealthConfig values",
            f"HEALTHCHECK --interval={health_config.check_interval_seconds}s --timeout={health_config.health_timeout_seconds}s --start-period=10s --retries=3 \\",
            "    CMD redis-cli ping || exit 1",
            "",
            "# Expose standard Redis port",
            "EXPOSE 6379",
            "",
        ])
        
        # Add custom user if specified
        if user_command:
            dockerfile_parts.append(user_command)
            dockerfile_parts.append("")
        
        dockerfile_parts.extend([
            "# Use mounted secrets for password",
            start_cmd
        ])
        
        return "\n".join(dockerfile_parts)

    @staticmethod
    def _generate_opensearch(service_name: str, port: int, project_name: str, env: Envs, 
                            health_config: HealthConfig, service_config: ServiceConfig) -> str:
        cluster_name = ContainerGenerator.generate_identifier(project_name, env, "cluster")
        node_name = ContainerGenerator.generate_identifier(project_name, env, "node")
        
        # Build custom sections
        package_install = service_config.get_package_install_command()
        environment_vars = service_config.get_environment_vars()
        setup_commands = service_config.get_setup_commands()
        user_command = service_config.get_user_command()
        
        # FIXED: Proper escaping while maintaining dynamic path
        if service_config.start_command:
            start_cmd = f'CMD {service_config.start_command}'
        else:
            secrets_file = SecretsManager.get_secrets_file()
            start_cmd = f'CMD ["sh", "-c", "export OPENSEARCH_INITIAL_ADMIN_PASSWORD=$(jq -r .opensearch {secrets_file}) && opensearch"]'
        
        dockerfile_parts = [
            "FROM opensearchproject/opensearch:2",
            "",
            "# Install jq for JSON parsing (as root, then switch back)",
            "USER root",
            "RUN yum install -y jq || (apt-get update && apt-get install -y jq)",
            "",
        ]
        
        # Add custom package installation
        if package_install:
            dockerfile_parts.extend([
                "# Install custom packages",
                package_install,
                "",
            ])
        
        # Switch back to opensearch user before other operations
        dockerfile_parts.extend([
            "# Switch back to opensearch user",
            "USER opensearch",
            "",
            "# Set environment variables with readable identifiers",
            "ENV discovery.type=single-node",
            "ENV plugins.security.disabled=true",
            f"ENV cluster.name={cluster_name}",
            f"ENV node.name={node_name}",
            "",
        ])
        
        # Add custom environment variables
        if environment_vars:
            dockerfile_parts.extend([
                "# Custom environment variables",
                environment_vars,
                "",
            ])
        
        # Add custom setup commands (as opensearch user)
        if setup_commands:
            dockerfile_parts.extend([
                "# Custom setup commands",
                setup_commands,
                "",
            ])
        
        dockerfile_parts.extend([
            f"# Health check using HealthConfig values",
            f"HEALTHCHECK --interval={health_config.check_interval_seconds}s --timeout={health_config.health_timeout_seconds}s --start-period=60s --retries=3 \\",
            "    CMD curl -f http://localhost:9200/_cluster/health || exit 1",
            "",
            "# Expose standard OpenSearch port",
            "EXPOSE 9200",
            "",
        ])
        
        # Add custom user if specified (but warn about conflicts)
        if user_command:
            dockerfile_parts.extend([
                "# Custom user specification",
                user_command,
                "",
            ])
        
        dockerfile_parts.extend([
            "# Use mounted secrets for password",
            start_cmd
        ])
        
        return "\n".join(dockerfile_parts)

    @staticmethod
    def _generate_nginx(service_name: str, port: int, project_name: str, env: Envs, 
                       health_config: HealthConfig, service_config: ServiceConfig) -> str:
        
        # Build custom sections
        package_install = service_config.get_package_install_command()
        environment_vars = service_config.get_environment_vars()
        setup_commands = service_config.get_setup_commands()
        user_command = service_config.get_user_command()
        
        # Determine start command
        if service_config.start_command:
            start_cmd = f'CMD {service_config.start_command}'
        else:
            start_cmd = 'CMD ["nginx", "-g", "daemon off;"]'
        
        dockerfile_parts = [
            "FROM nginx:alpine",
            "",
        ]
        
        # Add custom package installation (Alpine packages)
        if package_install:
            # Convert apt-get to apk for Alpine
            alpine_install = package_install.replace("apt-get update && apt-get install -y", "apk add --no-cache")
            alpine_install = alpine_install.replace("&& rm -rf /var/lib/apt/lists/*", "")
            dockerfile_parts.extend([
                "# Install custom packages",
                alpine_install,
                "",
            ])
        
        dockerfile_parts.extend([
            "# Copy nginx configuration",
            "COPY nginx.conf /etc/nginx/nginx.conf",
            "",
            "# Create directories for logs and configs",
            "RUN mkdir -p /var/log/nginx /etc/nginx/conf.d",
            "",
        ])
        
        # Add custom environment variables
        if environment_vars:
            dockerfile_parts.extend([
                "# Custom environment variables",
                environment_vars,
                "",
            ])
        
        # Add custom setup commands
        if setup_commands:
            dockerfile_parts.extend([
                "# Custom setup commands",
                setup_commands,
                "",
            ])
        
        dockerfile_parts.extend([
            f"# Health check using HealthConfig values",
            f"HEALTHCHECK --interval={health_config.check_interval_seconds}s --timeout={health_config.health_timeout_seconds}s --start-period=5s --retries=3 \\",
            "    CMD curl -f http://localhost:80/health || nginx -t",
            "",
            "# Expose standard nginx port",
            "EXPOSE 80",
            "",
        ])
        
        # Add custom user if specified
        if user_command:
            dockerfile_parts.extend([
                "# Custom user specification",
                user_command,
                "",
            ])
        
        dockerfile_parts.extend([
            "# Start nginx",
            start_cmd
        ])
        
        return "\n".join(dockerfile_parts)
    
    @staticmethod
    def generate_image_name(project_name: str, env: Envs, service_name: str) -> str:
        """
        Generate standardized image name ensuring uniqueness across environments.
        
        Creates image names in the format: {project}-{env}-{service}
        This ensures complete uniqueness and easy identification.
        
        Args:
            project_name: Name of the project (e.g., "ecommerce")
            env: Environment (DEV, TEST, UAT, PROD)
            service_name: Name of the service (e.g., "api", "maindb")
            
        Returns:
            str: Standardized image name
            
        Examples:
            ```python
            # Production API service
            name = ContainerGenerator.generate_image_name("ecommerce", Envs.PROD, "api")
            # Returns: "ecommerce-prod-api"
            
            # Development database
            name = ContainerGenerator.generate_image_name("ecommerce", Envs.DEV, "maindb") 
            # Returns: "ecommerce-dev-maindb"
            ```
        """
        env = Envs.to_enum(env)
        return f"{project_name}-{env.value}-{service_name}"
    
    @staticmethod
    def generate_container_name(project_name: str, env: Envs, service_name: str) -> str:
        """
        Generate standardized container name for running instances.
        
        Creates container names in the format: {project}_{env}_{service}
        Uses underscores to distinguish from image names (which use hyphens).
        
        Args:
            project_name: Name of the project
            env: Environment
            service_name: Name of the service
            
        Returns:
            str: Standardized container name
            
        Examples:
            ```python
            name = ContainerGenerator.generate_container_name("ecommerce", Envs.PROD, "api")
            # Returns: "ecommerce_prod_api"
            ```
        """
        env = Envs.to_enum(env)
        return f"{project_name}_{env.value}_{service_name}"
    
    @staticmethod
    def generate_volume_name(project_name: str, env: Envs, service_name: str, volume_type: str = "data") -> str:
        """
        Generate standardized volume name for persistent storage.
        
        Creates volume names in the format: {project}_{env}_{service}_{type}
        
        Args:
            project_name: Name of the project
            env: Environment
            service_name: Name of the service
            volume_type: Type of volume (default: "data")
            
        Returns:
            str: Standardized volume name
            
        Examples:
            ```python
            name = ContainerGenerator.generate_volume_name("ecommerce", Envs.PROD, "maindb")
            # Returns: "ecommerce_prod_maindb_data"
            
            name = ContainerGenerator.generate_volume_name("ecommerce", Envs.PROD, "nginx", "logs")
            # Returns: "ecommerce_prod_nginx_logs"
            ```
        """
        env = Envs.to_enum(env)
        return f"{project_name}_{env.value}_{service_name}_{volume_type}"

    @staticmethod
    def generate_container_network_command(project_name: str, env: Envs) -> str:
        """Generate Docker network create command for the project/environment."""
        network_name = ContainerGenerator.generate_network_name(project_name, env)
        return f"docker network create {network_name}"

    @staticmethod
    def generate_container_run_command(service_type: ServiceTypes, project_name: str, env: Envs, service_name: str, tag: str = "latest") -> str:
        """
        Generate Docker run command for the specified service type.
        
        Creates production-ready docker run commands with proper port mappings,
        environment variables, volume mounts, and service-specific configurations.
        Uses unified naming convention for complete uniqueness.
        
        Args:
            service_type: Type of service to generate run command for
            project_name: Name of the project (used for port calculation and identifiers)
            env: Environment (DEV, TEST, UAT, PROD)
            service_name: Name of the specific service instance
            tag: Image tag to use (default: "latest")
            
        Returns:
            str: Complete docker run command ready for execution
            
        Raises:
            ValueError: If service_type is not supported
            
        Examples:
            ```python
            # Web service run command
            run_cmd = ContainerGenerator.generate_container_run_command(
                ServiceTypes.WEB, "ecommerce", Envs.PROD, "api"
            )
            
            # Database run command with timestamp tag
            run_cmd = ContainerGenerator.generate_container_run_command(
                ServiceTypes.POSTGRES, "ecommerce", Envs.PROD, "maindb", "20250620-151702"
            )
            ```
            
        Command Features:
            - Unified naming convention ensuring uniqueness
            - Deterministic port mappings based on service type and project
            - Proper volume mounts for data persistence (databases)
            - Environment-specific container naming
            - Service-specific environment variables and configurations
            - Network isolation and security considerations
            - Restart policies for production environments
        """
        env = Envs.to_enum(env)
        port = ContainerGenerator.hash_port(service_type, project_name, env)
        
        # Use unified naming convention
        container_name = ContainerGenerator.generate_container_name(project_name, env, service_name)
        image_name = ContainerGenerator.generate_image_name(project_name, env, service_name)
        full_image_name = f"{image_name}:{tag}"        
        network_name = ContainerGenerator.generate_network_name(project_name, env)

        if service_type == ServiceTypes.WEB:
            return ContainerGenerator._generate_web_run_command(container_name, port, project_name, env, service_name, full_image_name, network_name)
        
        elif service_type == ServiceTypes.WORKER:
            return ContainerGenerator._generate_worker_run_command(container_name, project_name, env, service_name, full_image_name, network_name)
        
        elif service_type == ServiceTypes.POSTGRES:
            return ContainerGenerator._generate_postgres_run_command(container_name, port, project_name, env, service_name, full_image_name, network_name)
        
        elif service_type == ServiceTypes.REDIS:
            return ContainerGenerator._generate_redis_run_command(container_name, port, project_name, env, service_name, full_image_name, network_name)
        
        elif service_type == ServiceTypes.OPENSEARCH:
            return ContainerGenerator._generate_opensearch_run_command(container_name, port, project_name, env, service_name, full_image_name, network_name)
        
        elif service_type == ServiceTypes.NGINX:
            return ContainerGenerator._generate_nginx_run_command(container_name, port, service_name, full_image_name, network_name)
        
        else:
            raise ValueError(f"Unknown service type: {service_type}")

    @staticmethod
    def __helper(container_name, image_name, json_path, secrets_file, port=None, volume_bit='', network_name: str=None):
        res = f"docker run -d --name {container_name} --restart unless-stopped"
        if network_name:
            res = f"{res} --network {network_name}"
        if port:
            res = f"{res} -p {port}:{port}"
        sec = ''
        if json_path:
            sec = f'-v {json_path}:{secrets_file}:ro' 
        res = f"{res} {sec} {volume_bit} {image_name}"
        return res
    
    @staticmethod
    def _generate_web_run_command(container_name: str, port: int, project_name: str, env: Envs, service_name: str, image_name: str, network_name: str) -> str:
        secrets_file = SecretsManager.get_secrets_file()
        json_path = SecretsManager.get_json_path(project_name, env)
        
        return ContainerGenerator.__helper(container_name, image_name, json_path, secrets_file, port,'', network_name)

    @staticmethod
    def _generate_worker_run_command(container_name: str, project_name: str, env: Envs, service_name: str, image_name: str, network_name: str) -> str:
        secrets_file = SecretsManager.get_secrets_file()
        json_path = SecretsManager.get_json_path(project_name, env)
        
        return ContainerGenerator.__helper(container_name, image_name, json_path, secrets_file,None,'', network_name)

    @staticmethod
    def _generate_postgres_run_command(container_name: str, port: int, project_name: str, env: Envs, service_name: str, image_name: str, network_name: str) -> str:
        secrets_file = SecretsManager.get_secrets_file()
        json_path = SecretsManager.get_json_path(project_name, env)
        volume_name = ContainerGenerator.generate_volume_name(ContainerGenerator._get_project_from_container_name(container_name), 
                                               ContainerGenerator._get_env_from_container_name(container_name), 
                                               ContainerGenerator._get_service_from_container_name(container_name))
        return ContainerGenerator.__helper(container_name, image_name, json_path, secrets_file, port, f"-v {volume_name}:/var/lib/postgresql/data", network_name)

    @staticmethod
    def _generate_redis_run_command(container_name: str, port: int, project_name: str, env: Envs, service_name: str, image_name: str, network_name: str) -> str:
        secrets_file = SecretsManager.get_secrets_file()
        json_path = SecretsManager.get_json_path(project_name, env)
        volume_name = ContainerGenerator.generate_volume_name(project_name, env, service_name)
        
        return ContainerGenerator.__helper(container_name, image_name, json_path, secrets_file, port, f"-v {volume_name}:/data", network_name)

    @staticmethod
    def _generate_opensearch_run_command(container_name: str, port: int, project_name: str, env: Envs, service_name: str, image_name: str, network_name: str) -> str:
        secrets_file = SecretsManager.get_secrets_file()
        json_path = SecretsManager.get_json_path(project_name, env)
        volume_name = ContainerGenerator.generate_volume_name(project_name, env, service_name)
        
        return ContainerGenerator.__helper(container_name, image_name, json_path, secrets_file, port, f"-v {volume_name}:/usr/share/opensearch/data", network_name)
    
    @staticmethod
    def _generate_nginx_run_command(container_name: str, port: int, service_name: str, image_name: str, network_name: str) -> str:
        # Extract project and env from container name for volume naming
        parts = container_name.split('_')
        project_name = parts[0]
        env_value = parts[1]
        volume_name = f"{project_name}_{env_value}_{service_name}_logs"
        
        return ContainerGenerator.__helper(container_name, image_name, '', '', port, f"-v {volume_name}:/var/log/nginx", network_name)
    
    @staticmethod
    def _get_project_from_container_name(container_name: str) -> str:
        """Extract project name from container name format: project_env_service"""
        return container_name.split('_')[0]
    
    @staticmethod
    def _get_env_from_container_name(container_name: str) -> str:
        """Extract environment from container name format: project_env_service"""
        return Envs(container_name.split('_')[1])
    
    @staticmethod
    def _get_service_from_container_name(container_name: str) -> str:
        """Extract service name from container name format: project_env_service"""
        return container_name.split('_')[2]