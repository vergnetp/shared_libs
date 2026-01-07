"""
Deployer - Deployment orchestrator.

Clean, focused orchestrator that coordinates deployment operations.
Uses context for configuration, delegates to specialized clients.
"""

from __future__ import annotations
import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

if TYPE_CHECKING:
    from ..context import DeploymentContext
    from ..storage import StorageBackend

from .service import Service, ServiceType
from .result import Result, DeployResult, ContainerResult, BuildResult, Status
from ..docker.client import DockerClient
from ..ssh.client import SSHClient


class Deployer:
    """
    Deployment orchestrator.
    
    Coordinates the full deployment lifecycle:
    1. Load project configuration
    2. Provision servers (if needed)
    3. Build and push images
    4. Deploy containers
    5. Configure networking
    6. Verify health
    
    Usage:
        from backend.infra import DeploymentContext, Deployer
        
        ctx = DeploymentContext(
            user_id="workspace_123",
            project_name="myapp",
            env="prod",
            storage=storage,
        )
        
        deployer = Deployer(ctx)
        
        # Deploy all services
        result = deployer.deploy()
        
        # Deploy specific services
        result = deployer.deploy(services=["api", "worker"])
        
        # Just build
        result = deployer.build()
        
        # Rollback
        result = deployer.rollback(service="api")
    """
    
    def __init__(self, ctx: 'DeploymentContext'):
        """
        Initialize deployer.
        
        Args:
            ctx: Deployment context with user_id, project_name, env, storage
        """
        self.ctx = ctx
        self.docker = DockerClient(ctx)
        self.ssh = SSHClient(ctx)
        
        # Lazy-loaded
        self._project_config: Optional[Dict[str, Any]] = None
        self._services: Optional[Dict[str, Service]] = None
        self._credentials: Optional[Dict[str, Any]] = None
    
    # =========================================================================
    # Configuration Loading
    # =========================================================================
    
    async def _load_project(self) -> Dict[str, Any]:
        """Load project configuration from storage."""
        if self._project_config is None:
            if self.ctx.storage:
                self._project_config = await self.ctx.storage.get_project(
                    self.ctx.user_id,
                    self.ctx.project_name,
                ) or {}
            else:
                self._project_config = {}
        return self._project_config
    
    async def _load_credentials(self) -> Dict[str, Any]:
        """Load credentials from storage."""
        if self._credentials is None:
            if self.ctx.storage:
                self._credentials = await self.ctx.storage.get_credentials(
                    self.ctx.user_id,
                    self.ctx.project_name,
                    self.ctx.env,
                ) or {}
            else:
                self._credentials = {}
        return self._credentials
    
    def set_credentials(self, credentials: Dict[str, Any]) -> 'Deployer':
        """Set credentials directly (for API usage)."""
        self._credentials = credentials
        return self
    
    async def _get_services(self) -> Dict[str, Service]:
        """Get services from project config."""
        if self._services is None:
            config = await self._load_project()
            services_config = config.get("services", {})
            
            # Also check env-specific services
            env_config = config.get("environments", {}).get(self.ctx.env, {})
            env_services = env_config.get("services", {})
            
            # Merge (env-specific overrides)
            merged = {**services_config, **env_services}
            
            self._services = {}
            for name, svc_config in merged.items():
                if isinstance(svc_config, dict):
                    svc_config["name"] = name
                    self._services[name] = Service.from_dict(svc_config)
        
        return self._services
    
    # =========================================================================
    # Build Operations
    # =========================================================================
    
    def build(
        self,
        services: Optional[List[str]] = None,
        push: bool = True,
        no_cache: bool = False,
    ) -> DeployResult:
        """
        Build Docker images.
        
        Args:
            services: Services to build (None = all that need building)
            push: Push to registry after build
            no_cache: Force rebuild without cache
            
        Returns:
            DeployResult with build status per service
        """
        result = DeployResult(
            success=True,
            status=Status.RUNNING,
            started_at=datetime.utcnow(),
        )
        
        # Run async loading in sync context
        all_services = asyncio.get_event_loop().run_until_complete(
            self._get_services()
        )
        
        # Filter to requested services
        to_build = {}
        for name, svc in all_services.items():
            if services and name not in services:
                continue
            if svc.needs_build:
                to_build[name] = svc
        
        if not to_build:
            result.message = "No services need building"
            result.status = Status.SUCCESS
            result.completed_at = datetime.utcnow()
            return result
        
        self.ctx.log_info(f"Building {len(to_build)} services", services=list(to_build.keys()))
        
        # Build each service
        for name, svc in to_build.items():
            build_result = self._build_service(svc, no_cache=no_cache)
            
            if build_result.success and push:
                push_result = self._push_image(svc)
                if not push_result.success:
                    build_result.success = False
                    build_result.error = push_result.error
            
            result.services[name] = ContainerResult(
                success=build_result.success,
                message=build_result.message,
                error=build_result.error,
            )
            
            if not build_result.success:
                result.success = False
                result.error = f"Failed to build {name}: {build_result.error}"
        
        result.status = Status.SUCCESS if result.success else Status.FAILED
        result.completed_at = datetime.utcnow()
        
        return result
    
    def _build_service(self, service: Service, no_cache: bool = False) -> BuildResult:
        """Build a single service image."""
        image_name = self.ctx.image_name(service.name)
        
        self.ctx.log_info(f"Building {service.name}", image=image_name)
        
        # Generate Dockerfile if needed
        dockerfile_path = self._prepare_dockerfile(service)
        
        if not dockerfile_path:
            return BuildResult(
                success=False,
                error=f"No Dockerfile for {service.name}",
            )
        
        # Build
        build_result = self.docker.build(
            tag=image_name,
            dockerfile=dockerfile_path,
            context=service.build_context or ".",
            no_cache=no_cache,
        )
        
        if build_result.success:
            return BuildResult(
                success=True,
                message=f"Built {image_name}",
                image_name=image_name,
            )
        else:
            return BuildResult(
                success=False,
                error=build_result.error,
                image_name=image_name,
            )
    
    def _push_image(self, service: Service) -> Result:
        """Push service image to registry."""
        image_name = self.ctx.image_name(service.name)
        
        # Login to registry if credentials available
        if self._credentials:
            registry_user = self._credentials.get("registry_username")
            registry_pass = self._credentials.get("registry_password")
            if registry_user and registry_pass:
                self.docker.login(registry_user, registry_pass)
        
        return self.docker.push(image_name)
    
    def _prepare_dockerfile(self, service: Service) -> Optional[str]:
        """Prepare Dockerfile for building. Returns path to Dockerfile."""
        if service.dockerfile:
            # Use provided Dockerfile path or content
            if isinstance(service.dockerfile, str) and len(service.dockerfile) < 256:
                # Looks like a path
                return service.dockerfile
            else:
                # Content - write to temp file
                import tempfile
                import os
                
                temp_dir = tempfile.mkdtemp(prefix=f"build_{service.name}_")
                dockerfile_path = os.path.join(temp_dir, "Dockerfile")
                
                with open(dockerfile_path, "w") as f:
                    f.write(service.dockerfile)
                
                return dockerfile_path
        
        elif service.image:
            # Pre-built image, no dockerfile needed
            return None
        
        else:
            # Auto-generate based on service type
            return self._generate_dockerfile(service)
    
    def _generate_dockerfile(self, service: Service) -> Optional[str]:
        """Generate Dockerfile based on service type."""
        import tempfile
        import os
        
        content = None
        
        if service.type == ServiceType.PYTHON:
            content = f"""FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD {service.command or '["python", "main.py"]'}
"""
        elif service.type == ServiceType.NODE:
            content = f"""FROM node:20-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci --only=production
COPY . .
CMD {service.command or '["node", "index.js"]'}
"""
        elif service.type == ServiceType.REACT:
            content = """FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/build /usr/share/nginx/html
"""
        
        if content:
            temp_dir = tempfile.mkdtemp(prefix=f"build_{service.name}_")
            dockerfile_path = os.path.join(temp_dir, "Dockerfile")
            with open(dockerfile_path, "w") as f:
                f.write(content)
            return dockerfile_path
        
        return None
    
    # =========================================================================
    # Deploy Operations
    # =========================================================================
    
    def deploy(
        self,
        services: Optional[List[str]] = None,
        build: bool = True,
        servers: Optional[List[str]] = None,
    ) -> DeployResult:
        """
        Deploy services.
        
        Args:
            services: Services to deploy (None = all)
            build: Build images first
            servers: Target servers (None = from config)
            
        Returns:
            DeployResult with status per service
        """
        result = DeployResult(
            success=True,
            status=Status.RUNNING,
            started_at=datetime.utcnow(),
        )
        
        try:
            # Load services
            all_services = asyncio.get_event_loop().run_until_complete(
                self._get_services()
            )
            
            # Filter to requested services
            to_deploy = {}
            for name, svc in all_services.items():
                if services and name not in services:
                    continue
                to_deploy[name] = svc
            
            if not to_deploy:
                result.message = "No services to deploy"
                result.status = Status.SUCCESS
                result.completed_at = datetime.utcnow()
                return result
            
            self.ctx.log_info(
                f"Deploying {len(to_deploy)} services",
                services=list(to_deploy.keys()),
            )
            
            # Build phase
            if build:
                buildable = [n for n, s in to_deploy.items() if s.needs_build]
                if buildable:
                    build_result = self.build(services=buildable, push=True)
                    if not build_result.success:
                        result.success = False
                        result.error = build_result.error
                        result.status = Status.FAILED
                        result.completed_at = datetime.utcnow()
                        return result
            
            # Sort by startup order
            ordered = sorted(
                to_deploy.items(),
                key=lambda x: (x[1].startup_order, x[0])
            )
            
            # Deploy each service
            for name, svc in ordered:
                svc_result = self._deploy_service(svc, servers=servers)
                result.services[name] = svc_result
                
                if not svc_result.success:
                    result.success = False
                    result.error = f"Failed to deploy {name}: {svc_result.error}"
                    # Continue deploying other services or stop?
                    # For now, continue
            
            result.status = Status.SUCCESS if result.success else Status.FAILED
            result.message = f"Deployed {len(to_deploy)} services"
            
        except Exception as e:
            result.success = False
            result.status = Status.FAILED
            result.error = str(e)
            self.ctx.log_error(f"Deploy failed: {e}")
        
        result.completed_at = datetime.utcnow()
        
        # Save state
        asyncio.get_event_loop().run_until_complete(
            self._save_deployment_state(result)
        )
        
        return result
    
    def _deploy_service(
        self,
        service: Service,
        servers: Optional[List[str]] = None,
    ) -> ContainerResult:
        """Deploy a single service."""
        self.ctx.log_info(f"Deploying {service.name}")
        
        # Determine target servers
        target_servers = servers or self._get_target_servers(service)
        
        if not target_servers:
            return ContainerResult(
                success=False,
                error="No target servers",
                container_name=service.name,
            )
        
        # Determine image
        image = service.image or self.ctx.image_name(service.name)
        
        # Deploy to each server
        results = []
        for server in target_servers:
            container_result = self._deploy_to_server(service, server, image)
            results.append(container_result)
        
        # Check if all succeeded
        success = all(r.success for r in results)
        
        return ContainerResult(
            success=success,
            message=f"Deployed to {len(target_servers)} servers" if success else None,
            error=results[0].error if not success else None,
            container_name=self.ctx.container_name(service.name),
            server_ip=target_servers[0] if len(target_servers) == 1 else None,
        )
    
    def _deploy_to_server(
        self,
        service: Service,
        server: str,
        image: str,
    ) -> ContainerResult:
        """Deploy service to a single server."""
        container_name = self.ctx.container_name(service.name)
        
        # Prepare environment
        env = dict(service.environment)
        
        # Add standard env vars
        env["SERVICE_NAME"] = service.name
        env["PROJECT_NAME"] = self.ctx.project_name
        env["ENVIRONMENT"] = self.ctx.env
        
        # Inject secrets
        if service.secrets and self._credentials:
            for secret_name in service.secrets:
                if secret_name in self._credentials:
                    env[secret_name.upper()] = self._credentials[secret_name]
        
        # Prepare ports
        ports = {}
        for port in service.ports:
            host_port = port.host_port or self._allocate_port(service, port.container_port)
            ports[port.container_port] = host_port
        
        # Prepare volumes
        volumes = {}
        for vol in service.volumes:
            if vol.type == "bind":
                host_path = vol.name
            else:
                # Named volume - use full name
                host_path = self.ctx.volume_name(service.name, vol.name)
            volumes[host_path] = vol.container_path
        
        # Ensure network exists
        self.docker.network_create(self.ctx.network_name, server=server)
        
        # Pull image on remote server
        if server != "localhost":
            pull_result = self.docker.pull(image, server=server)
            if not pull_result.success:
                return ContainerResult(
                    success=False,
                    error=f"Failed to pull image: {pull_result.error}",
                    container_name=container_name,
                    server_ip=server,
                )
        
        # Run container
        result = self.docker.run(
            image=image,
            name=container_name,
            server=server,
            ports=ports if not service.internal else None,
            environment=env,
            volumes=volumes,
            network=self.ctx.network_name,
            command=service.command,
            entrypoint=service.entrypoint,
            memory=service.memory,
            cpus=service.cpus,
            restart=service.restart_policy.value,
            labels={
                "project": self.ctx.project_name,
                "env": self.ctx.env,
                "service": service.name,
                "user": self.ctx.user_id,
            },
        )
        
        if result.success:
            # Verify health
            if service.health_check:
                healthy = self._wait_for_health(service, server, container_name)
                if not healthy:
                    result.success = False
                    result.error = "Health check failed"
        
        return result
    
    def _get_target_servers(self, service: Service) -> List[str]:
        """Get target servers for a service."""
        # For now, default to localhost
        # TODO: Integrate with server inventory
        if service.zone == "localhost":
            return ["localhost"]
        
        # Get from storage
        servers = asyncio.get_event_loop().run_until_complete(
            self._get_servers_for_zone(service.zone)
        )
        
        if servers:
            return servers[:service.servers_count]
        
        return ["localhost"]
    
    async def _get_servers_for_zone(self, zone: str) -> List[str]:
        """Get server IPs for a zone."""
        if not self.ctx.storage:
            return []
        
        servers = await self.ctx.storage.get_servers(
            self.ctx.user_id,
            zone=zone,
        )
        
        return [s["ip"] for s in servers if s.get("status") == "active"]
    
    def _allocate_port(self, service: Service, container_port: int) -> int:
        """Allocate a host port for a service."""
        # Simple hash-based allocation
        # TODO: Check for conflicts
        import hashlib
        
        key = f"{self.ctx.namespace}_{service.name}_{container_port}"
        hash_val = int(hashlib.md5(key.encode()).hexdigest()[:8], 16)
        
        return 8000 + (hash_val % 1000)
    
    def _wait_for_health(
        self,
        service: Service,
        server: str,
        container_name: str,
        timeout: int = 60,
    ) -> bool:
        """Wait for container to become healthy."""
        import time
        
        if not service.health_check:
            return True
        
        start = time.time()
        
        while time.time() - start < timeout:
            if self.docker.is_running(container_name, server):
                # TODO: Implement actual health check based on service.health_check
                return True
            time.sleep(2)
        
        return False
    
    # =========================================================================
    # State Management
    # =========================================================================
    
    async def _save_deployment_state(self, result: DeployResult) -> None:
        """Save deployment state to storage."""
        if not self.ctx.storage:
            return
        
        state = {
            "last_deployment": {
                "success": result.success,
                "status": result.status.value,
                "started_at": result.started_at.isoformat() if result.started_at else None,
                "completed_at": result.completed_at.isoformat() if result.completed_at else None,
                "services": {
                    name: {
                        "success": svc.success,
                        "container_name": svc.container_name,
                        "server_ip": svc.server_ip,
                        "error": svc.error,
                    }
                    for name, svc in result.services.items()
                },
            },
        }
        
        await self.ctx.storage.save_deployment_state(
            self.ctx.user_id,
            self.ctx.project_name,
            self.ctx.env,
            state,
        )
    
    # =========================================================================
    # Status & Logs
    # =========================================================================
    
    def status(self) -> Dict[str, Any]:
        """Get current deployment status."""
        services = asyncio.get_event_loop().run_until_complete(
            self._get_services()
        )
        
        status = {}
        for name, svc in services.items():
            container_name = self.ctx.container_name(name)
            containers = self.docker.ps(filter_name=container_name)
            
            status[name] = {
                "container_name": container_name,
                "running": any(c.is_running for c in containers),
                "containers": [
                    {
                        "id": c.id,
                        "status": c.status,
                        "ports": c.ports,
                    }
                    for c in containers
                ],
            }
        
        return status
    
    def logs(self, service: str, lines: int = 100) -> str:
        """Get service logs."""
        container_name = self.ctx.container_name(service)
        return self.docker.logs(container_name, lines=lines)
    
    # =========================================================================
    # Rollback
    # =========================================================================
    
    def rollback(
        self,
        service: Optional[str] = None,
        version: Optional[str] = None,
    ) -> DeployResult:
        """
        Rollback to previous deployment.
        
        Args:
            service: Specific service (None = all)
            version: Target version (None = previous)
            
        Returns:
            DeployResult
        """
        # TODO: Implement rollback logic
        # - Track previous container names/images
        # - Stop current containers
        # - Start previous version
        
        return DeployResult(
            success=False,
            status=Status.FAILED,
            message="Rollback not yet implemented",
        )
    
    # =========================================================================
    # Async Wrappers
    # =========================================================================
    
    async def deploy_async(
        self,
        services: Optional[List[str]] = None,
        build: bool = True,
    ) -> DeployResult:
        """Async version of deploy."""
        return await asyncio.to_thread(
            self.deploy,
            services=services,
            build=build,
        )
    
    async def build_async(
        self,
        services: Optional[List[str]] = None,
        push: bool = True,
    ) -> DeployResult:
        """Async version of build."""
        return await asyncio.to_thread(
            self.build,
            services=services,
            push=push,
        )
    
    async def status_async(self) -> Dict[str, Any]:
        """Async version of status."""
        return await asyncio.to_thread(self.status)
    
    async def logs_async(self, service: str, lines: int = 100) -> str:
        """Async version of logs."""
        return await asyncio.to_thread(self.logs, service, lines)
