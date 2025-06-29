

from enum import Enum
from typing import List, Dict, Optional

class Regions(Enum):
    LONDON = "lon1"  # Use actual DO region codes
    PARIS = "par1"
    NYC = "nyc1"

class Envs(Enum):
    DEV = "dev"
    TEST = "test" 
    UAT = "uat"
    PROD = "prod"

class ServiceTypes(Enum):
    WEB = "web"
    WORKER = "worker"
    INFRASTRUCTURE = "infrastructure"
    LOAD_BALANCER = "load_balancer"

class Server:
    def __init__(self, name: str, role: str = "web", cpu_number: int=1, memory_gb: int=1, region: Regions=Regions.LONDON):
        self.name = name
        self.ip: Optional[str] = None
        self.cpu_number = cpu_number
        self.memory_gb = memory_gb
        self.region = region
        self.role = role  # "master", "web"
        
    def size_slug(self) -> str:
        """Convert to DigitalOcean size slug"""
        return f"s-{self.cpu_number}vcpu-{self.memory_gb}gb"

class HealthConfig:
    def __init__(self):
        self.interval_minutes = 5
        self.check_interval_seconds = 30
        self.failure_timeout_minutes = 3
        self.health_timeout_seconds = 10

def hash_port(service_type, project_name, env):
    if service_type ==  ServiceTypes.WORKER:
        return None
    return 'to do'

def create_droplet() -> str:
    #todo
    return 'todo'

def drop_droplet(ip: str) -> bool:
    #todo
    return True

class Service:
    def __init__(self, name: str, env: Envs, service_type: ServiceTypes, servers: List[Server] = [], port: Optional[int] = None, secret_variables: List[str] = [], health_config: HealthConfig=HealthConfig()):
        self.name = name
        self.type = service_type
        self.env = env
        self.servers = servers
        if not port:
            port = hash_port(service_type, name, env)
        self.port = port     
        self.secret_variables= secret_variables
        self.health_config = health_config
        
    @property
    def containerfile_path(self) -> str:
        """Convention: containerfiles/{service_name}"""
        return f"containerfiles/{self.name}"

class Project:
    def __init__(self, name: str):
        self.name = name
        self.services: List[Service] = []

class Infrastructure:
    def __init__(self):
        self._servers: List[Server] = []
        self._projects: List[Project] = []
        # load state from json

    def create_server(self, name: str, role: str = "web", cpu_number: int=1, memory_gb: int=1, region: Regions=Regions.LONDON) -> Server:
        # check name not in self._servers and not in DO
        server = Server(name, role, cpu_number, memory_gb, region)
        ip = create_droplet()
        server.ip = ip
        self._servers.append(server)
        # serialize to json   
        return server     

    def drop_server(self, name: str):
        # check name in self._servers and DO
        # remove from DO
        # remove from self._servers
        # serialize to json  
        pass

    def update_server(self, name: str, role: str = "web", cpu_number: int=1, memory_gb: int=1, region: Regions=Regions.LONDON) -> Server:
        ip = create_droplet()       
        # get all services on name
        # deploy them on ip
        self.drop_server(name)
        server = Server(name, role, cpu_number, memory_gb, region)
        server.ip = ip
        self._servers.append(server)
        # serialize to json   
        return server    
    
    def add_service(self, project_name: str, service_name: str, env: Envs, service_type: ServiceTypes, servers: List[Server] = [], port: Optional[int] = None, secret_variables: List[str] = [], health_config: HealthConfig=HealthConfig()) -> bool:
        for project in self._projects:
            if project.name == project_name:
                # check there is no service with same name and env in the project
                service = Service(service_name, env, service_type, servers, port, secret_variables, health_config)
                project.services.append(service)
                 # deploy and launch on all servers
                # serialize to json 
                return True
        return False
    
    def drop_service(self, service_name: str, project_name: str = None,  env: Envs = None):
        found = False
        for project in self._projects:
            if project.name == project_name or project_name is None:
                for service in project.services:
                    if service.env == env or env is None:
                        if service.name == service_name:
                            found = True
                            # stop and remove from all servers
                            project.services.remove(service)
        # serialize to json 
        return found

    def update_service(self, service_name: str, project_name: str = None,  env: Envs = None, service_type: ServiceTypes = None, servers: List[Server] = None, port: Optional[int] = None, secret_variables: List[str] = None, health_config: HealthConfig = None) -> bool:
        found = False
        for project in self._projects:
            if project.name == project_name or project_name is None:
                for service in project.services:
                    if service.env == env or env is None:
                        if service.name == service_name:
                            # stop and remove from all servers
                            if service_type:
                                service.service_type = service_type
                            if servers:
                                for server in service.servers:
                                    self.drop_server(server)
                                for server in servers:
                                    if not server.ip:
                                        server = self.create_server(server.name, server.role, server.cpu_number, server.memory_gb, server.region)
                                    self.add_server(server) 
                                service.servers = servers
                                # update nginx etc
                            if port:
                                service.port = port
                                # update nginx etc
                            if secret_variables:
                                service.secret_variables = secret_variables
                            if health_config:
                                service.health_config = health_config
                                # relaunch the health checks
                            # deploy and launch on all servers
        # serialize to json
        return found

    def deploy_service(self, service_name: str, project_name: str,  env: Envs) -> bool:
        # we probably can't allow all envs as we need to enforce testing
        found = False
        for project in self._projects:
            if project.name == project_name:
                for service in project.services:
                    if service.env == env:
                        if service.name == service_name:
                            found = True
                            # deploy and launch on all servers
        return found
    
    def deploy_project(self, project_name: str,  env: Envs) -> bool:
        # we probably can't allow all envs as we need to enforce testing
        found = False
        for project in self._projects:
            if project.name == project_name:
                for service in project.services:
                    found = found or self.deploy_service(service.name, project_name, env)
        return found        

    def scale_service(self, service_name: str, project_name: str,  env: Envs, nb_servers: int, role: str = "web", cpu_number: int=1, memory_gb: int=1, region: Regions=Regions.LONDON) -> bool:
        found = False
        for project in self._projects:
            if project.name == project_name:
                for service in project.services:
                    if service.env == env:
                        if service.name == service_name: 
                            found = True
                            if len(service.servers) < nb_servers:                                
                                for i in range(0, nb_servers - len(service.servers)):
                                    server = self.create_server(f'{role}{len(service.servers)+i+1}', role=role, cpu_number=cpu_number, memory_gb=memory_gb, region=region)
                                    # update nginx etc
                                    # deploy and launch service on new server
                                    service.servers.append(server)
                            else:
                                while (len(service.servers) > nb_servers):
                                    server = service.servers.pop()
                                    self.drop_server(server.name)
        # serialize to json
        return found
                                

    def scale_project(self, project_name: str,  env: Envs, nb_servers: int, role: str = "web", cpu_number: int=1, memory_gb: int=1, region: Regions=Regions.LONDON) -> bool:
        found = False
        for project in self._projects:
            if project.name == project_name:
                found = True
                for service in project.services:
                    self.scale_service(service.name, project_name, env, nb_servers, role, cpu_number, memory_gb, region)
        return found
    



import hashlib

def hash_port(service_type, project_name, env):
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

def _generate_web(service_type: ServiceTypes, project_name: str, env: Envs):
    port = hash_port(service_type, project_name, env)
    return f"""FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \\
    curl \\
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser \\
    && chown -R appuser:appuser /app
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \\
    CMD curl -f http://localhost:${{PORT:-8000}}/health || exit 1

# Expose port (will be set by deployment)
EXPOSE 8000

# Run the application
CMD ["python", "app.py"]
"""


def _generate_worker(project_name, secrets):
    secrets_check = ""
    if secrets:
        # Generate health check that verifies worker can access required services
        secrets_check = f"""
# Health check: Verify worker process and dependencies
HEALTHCHECK --interval=60s --timeout=15s --start-period=10s --retries=3 \\
    CMD python -c "
import psutil
import sys
import os

# Check if worker process is running
worker_found = False
for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
    cmdline = ' '.join(proc.info['cmdline'] or [])
    if 'worker.py' in cmdline or '{project_name}' in cmdline:
        worker_found = True
        break

if not worker_found:
    print('Worker process not found')
    sys.exit(1)

# Check if we can access required services
try:
    # Add specific checks based on secrets
    {'# Check database connection' if any('db' in s.lower() for s in secrets) else ''}
    {'# Check Redis connection' if any('redis' in s.lower() for s in secrets) else ''}
    print('Worker health check passed')
    sys.exit(0)
except Exception as e:
    print(f'Worker health check failed: {{e}}')
    sys.exit(1)
"
"""
        
    return f"""FROM python:3.11-slim

# Install system dependencies including ps for health checks
RUN apt-get update && apt-get install -y \\
    procps \\
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser \\
    && chown -R appuser:appuser /app
USER appuser

{secrets_check}

# Run the worker
CMD ["python", "worker.py"]
"""

def _generate_infra():
    # For infrastructure, we typically use pre-built images
    return f"""# Infrastructure services typically use official images
# This Dockerfile is for custom infrastructure services only

FROM alpine:latest

# Install dependencies
RUN apk add --no-cache \\
    curl \\
    bash

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \\
    CMD curl -f http://localhost:8080/health || exit 1

# Default command
CMD ["sh", "-c", "echo 'Infrastructure service started' && sleep infinity"]
"""

def _generate_nginx():
    return f"""FROM nginx:alpine

# Copy nginx configuration
COPY nginx.conf /etc/nginx/nginx.conf

# Create directories for logs and configs
RUN mkdir -p /var/log/nginx /etc/nginx/conf.d

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \\
    CMD curl -f http://localhost:80/health || nginx -t

# Expose HTTP and HTTPS ports
EXPOSE 80 443

# Start nginx
CMD ["nginx", "-g", "daemon off;"]
"""

def _generate_postgres(service_name: str, port: int, health_config: HealthConfig):
    return f"""FROM postgres:15-alpine

# Set default environment variables (override at runtime)
ENV POSTGRES_DB=defaultdb
ENV POSTGRES_USER=postgres
ENV POSTGRES_PASSWORD=changeme

# Create custom init directory
RUN mkdir -p /docker-entrypoint-initdb.d

# Health check using HealthConfig values
HEALTHCHECK --interval={health_config.check_interval_seconds}s --timeout={health_config.health_timeout_seconds}s --start-period=30s --retries=3 \\
    CMD pg_isready -U $POSTGRES_USER -d $POSTGRES_DB -p {port}

# Expose calculated PostgreSQL port
EXPOSE {port}

# Use official entrypoint with custom port
CMD ["postgres", "-p", "{port}"]
"""

def _generate_redis(service_name: str, port: int, health_config: HealthConfig):
    return f"""FROM redis:7-alpine

# Health check using HealthConfig values
HEALTHCHECK --interval={health_config.check_interval_seconds}s --timeout={health_config.health_timeout_seconds}s --start-period=10s --retries=3 \\
    CMD redis-cli -p {port} ping || exit 1

# Expose calculated Redis port
EXPOSE {port}

# Use official entrypoint with custom port
CMD ["redis-server", "--port", "{port}"]
"""

def _generate_opensearch(service_name: str, port: int, health_config: HealthConfig):
    return f"""FROM opensearchproject/opensearch:2

# Set environment variables
ENV discovery.type=single-node
ENV plugins.security.disabled=true
ENV OPENSEARCH_INITIAL_ADMIN_PASSWORD=Admin123!
ENV http.port={port}

# Health check using HealthConfig values
HEALTHCHECK --interval={health_config.check_interval_seconds}s --timeout={health_config.health_timeout_seconds}s --start-period=60s --retries=3 \\
    CMD curl -f http://localhost:{port}/_cluster/health || exit 1

# Expose calculated OpenSearch port
EXPOSE {port}

# Use official entrypoint
CMD ["opensearch"]
"""

def generate_container_file_content(service_type: ServiceTypes, project_name: str, env: Envs, service_name: str, port: int, health_config: HealthConfig) -> str:
    """Generate Dockerfile content based on service type"""
    
    if service_type == ServiceTypes.WEB:
        return _generate_web(service_type, project_name, env, port, service_name, health_config)
    
    elif service_type == ServiceTypes.WORKER:
        return _generate_worker(project_name, env, service_name, health_config)
    
    elif service_type == ServiceTypes.POSTGRES:
        return _generate_postgres(service_name, port, health_config)
    
    elif service_type == ServiceTypes.REDIS:
        return _generate_redis(service_name, port, health_config)
    
    elif service_type == ServiceTypes.OPENSEARCH:
        return _generate_opensearch(service_name, port, health_config)
    
    elif service_type == ServiceTypes.NGINX:
        return _generate_nginx(service_name, port, health_config)
    
    else:
        raise ValueError(f"Unknown service type: {service_type}")

