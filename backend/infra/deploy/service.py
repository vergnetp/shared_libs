"""
Deployment Service - Orchestrates multi-server deployments.

All deployment logic lives here. API is just a thin wrapper.
"""

from __future__ import annotations
import asyncio
import aiohttp
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Callable, Literal
from enum import Enum

from ..node_agent.client import NodeAgentClient
from ..cloud.digitalocean import DOClient


class DeploySource(Enum):
    CODE = "code"           # Upload tar/zip
    GIT = "git"             # Clone from repo
    IMAGE = "image"         # Pull existing image from registry
    IMAGE_FILE = "image_file"  # Load image from uploaded tar
    
    @classmethod
    def from_value(cls, value: str) -> "DeploySource":
        """Look up enum by value string."""
        for member in cls:
            if member.value == value:
                return member
        raise ValueError(f"'{value}' is not a valid {cls.__name__}")


# Known stateful services (use TCP stream proxy, not HTTP)
STATEFUL_SERVICES = {
    "postgres", "postgresql", "mysql", "mariadb", 
    "redis", "mongo", "mongodb", "opensearch", "elasticsearch"
}


@dataclass
class MultiDeployConfig:
    """Deployment configuration for multi-server deploys."""
    # App config
    name: str  # Service name
    port: int = 8000
    container_port: Optional[int] = None  # For IMAGE_FILE: internal port
    host_port: Optional[int] = None       # For IMAGE_FILE: external port
    env_vars: Dict[str, str] = field(default_factory=dict)
    environment: str = "prod"  # prod/staging/dev/test/uat
    tags: List[str] = field(default_factory=list)
    
    # Project context (for unique container naming)
    project: Optional[str] = None  # Project name (defaults to service name)
    workspace_id: Optional[str] = None  # User/workspace ID
    
    # Service mesh / sidecar config
    depends_on: List[str] = field(default_factory=list)  # Services this depends on
    setup_sidecar: bool = True  # Set up nginx sidecar after deploy
    is_stateful: bool = False   # Is this a stateful service (postgres, redis, etc.)
    
    # Domain config
    setup_domain: bool = False  # Auto-provision domain
    cloudflare_token: Optional[str] = None  # Required if setup_domain=True
    base_domain: str = "digitalpixo.com"  # Base domain for subdomains
    domain_aliases: List[str] = field(default_factory=list)  # Custom domain aliases
    
    # Source config
    source_type: DeploySource = DeploySource.CODE
    
    # For CODE source
    code_tar: Optional[bytes] = None
    dockerfile: Optional[str] = None
    
    # For GIT source
    git_url: Optional[str] = None
    git_branch: str = "main"
    git_token: Optional[str] = None
    git_folders: Optional[List[Dict[str, Any]]] = None  # [{path: str, isMain: bool}]
    
    # For IMAGE source (registry)
    image: Optional[str] = None
    
    # For IMAGE_FILE source (local tar)
    image_tar: Optional[bytes] = None
    
    # Infrastructure
    server_ips: List[str] = field(default_factory=list)  # Existing servers
    new_server_count: int = 0
    snapshot_id: Optional[str] = None
    region: str = "lon1"
    size: str = "s-1vcpu-1gb"
    
    def __post_init__(self):
        """Auto-detect if this is a stateful service."""
        if self.name.lower() in STATEFUL_SERVICES:
            self.is_stateful = True


@dataclass
class ServerResult:
    """Result for a single server deployment."""
    ip: str
    name: str
    success: bool
    error: Optional[str] = None
    url: Optional[str] = None
    # Sidecar info
    internal_port: Optional[int] = None  # Port on nginx for service discovery
    sidecar_configured: bool = False


@dataclass 
class MultiDeployResult:
    """Overall deployment result."""
    success: bool
    servers: List[ServerResult] = field(default_factory=list)
    successful_count: int = 0
    failed_count: int = 0
    error: Optional[str] = None
    # Architecture info
    service_name: Optional[str] = None
    project: Optional[str] = None
    environment: Optional[str] = None
    container_name: Optional[str] = None
    internal_port: Optional[int] = None  # Sidecar port for service discovery
    depends_on: List[str] = field(default_factory=list)
    # Dependent containers that were restarted (when deploying stateful services)
    restarted_dependents: List[str] = field(default_factory=list)
    # Domain info
    domain: Optional[str] = None  # Auto-provisioned domain
    domain_aliases: List[str] = field(default_factory=list)  # Custom aliases
    
    @property
    def urls(self) -> List[str]:
        return [s.url for s in self.servers if s.success and s.url]


LogCallback = Callable[[str], None]


class DeploymentService:
    """
    Orchestrates deployments across multiple servers.
    
    Usage:
        service = DeploymentService(do_token="...", agent_key="...")
        
        result = await service.deploy(MultiDeployConfig(
            name="myapp",
            port=8000,
            source_type=DeploySource.GIT,
            git_url="https://github.com/user/repo",
            server_ips=["1.2.3.4", "5.6.7.8"],
        ))
    """
    
    def __init__(
        self,
        do_token: str,
        agent_key: str = "hostomatic-agent-key",
        log: LogCallback = None,
    ):
        self.do_token = do_token
        self.agent_key = agent_key
        self.log = log or (lambda msg: None)
        self._do_client: Optional[DOClient] = None
    
    @property
    def do_client(self) -> DOClient:
        if not self._do_client:
            self._do_client = DOClient(self.do_token)
        return self._do_client
    
    def _agent(self, ip: str) -> NodeAgentClient:
        """Create agent client for server."""
        return NodeAgentClient(ip, self.agent_key)
    
    @staticmethod
    def _ensure_tar(data: bytes) -> bytes:
        """Convert zip to tar.gz if needed. Agent expects tar.gz."""
        import io
        import zipfile
        import tarfile
        import time
        
        # Detect zip by magic bytes (PK = 0x50 0x4B)
        if data[:2] == b'PK':
            zip_buffer = io.BytesIO(data)
            tar_buffer = io.BytesIO()
            
            # Use current time for all files so Docker detects changes
            current_time = time.time()
            
            with zipfile.ZipFile(zip_buffer, 'r') as zf:
                with tarfile.open(fileobj=tar_buffer, mode='w:gz') as tf:
                    for zip_info in zf.infolist():
                        name = zip_info.filename
                        tar_info = tarfile.TarInfo(name=name)
                        tar_info.mtime = current_time  # Critical for Docker cache busting!
                        
                        if zip_info.is_dir():
                            tar_info.type = tarfile.DIRTYPE
                            tar_info.mode = 0o755
                            tf.addfile(tar_info)
                        else:
                            file_data = zf.read(name)
                            tar_info.size = len(file_data)
                            tar_info.mode = 0o644
                            tf.addfile(tar_info, io.BytesIO(file_data))
            
            return tar_buffer.getvalue()
        
        return data
    
    # =========================================================================
    # Main Deploy Method
    # =========================================================================
    
    async def deploy(self, config: MultiDeployConfig) -> MultiDeployResult:
        """
        Deploy to multiple servers.
        
        Flow:
        1. Collect servers (existing + provision new)
        2. Wait for all agents
        3. Prepare code on each server (upload or git clone)
        4. Build image on each server
        5. Run container on each server
        6. Setup sidecar for service discovery
        """
        try:
            # Validate
            if not config.server_ips and config.new_server_count == 0:
                return MultiDeployResult(success=False, error="No servers specified")
            
            if config.source_type == DeploySource.CODE:
                if not config.code_tar:
                    return MultiDeployResult(success=False, error="No code provided")
                if not config.dockerfile:
                    return MultiDeployResult(success=False, error="No Dockerfile provided")
            elif config.source_type == DeploySource.GIT:
                if not config.git_url:
                    return MultiDeployResult(success=False, error="No Git URL provided")
            elif config.source_type == DeploySource.IMAGE:
                if not config.image:
                    return MultiDeployResult(success=False, error="No image provided")
            elif config.source_type == DeploySource.IMAGE_FILE:
                if not config.image_tar:
                    return MultiDeployResult(success=False, error="No image tar provided")
                if not config.image:
                    return MultiDeployResult(success=False, error="No image name provided")
            
            # Step 1: Collect all servers
            servers = await self._collect_servers(config)
            if not servers:
                return MultiDeployResult(success=False, error="No servers available")
            
            total = len(servers)
            project_name = config.project or config.name
            self.log(f"🚀 Deploying {config.name} to {total} server{'s' if total > 1 else ''} [{config.environment}]")
            if config.depends_on:
                self.log(f"   Dependencies: {', '.join(config.depends_on)}")
            
            # Step 2: Wait for agents
            self.log(f"⏳ Checking node agents...")
            ready_servers = await self._wait_for_agents(servers)
            if not ready_servers:
                return MultiDeployResult(success=False, error="No agents ready")
            
            # Step 3: Get Dockerfile (for git deploys, need to clone first on one server)
            dockerfile = config.dockerfile
            if config.source_type == DeploySource.GIT and not dockerfile:
                dockerfile = await self._get_dockerfile_from_git(config, ready_servers[0])
                if not dockerfile:
                    return MultiDeployResult(success=False, error="Failed to get Dockerfile")
            
            # Step 4: Deploy to all servers (includes sidecar setup)
            self.log(f"🔨 Building and deploying...")
            results, container_name, internal_port = await self._deploy_to_servers(config, ready_servers, dockerfile)
            
            successful = [r for r in results if r.success]
            failed = [r for r in results if not r.success]
            successful_ips = [s.ip for s in successful]
            
            # Build result
            result = MultiDeployResult(
                success=len(successful) > 0,
                servers=results,
                successful_count=len(successful),
                failed_count=len(failed),
                # Architecture info
                service_name=config.name,
                project=project_name,
                environment=config.environment,
                container_name=container_name,
                internal_port=internal_port,
                depends_on=config.depends_on,
            )
            
            # Step 5: Setup domain (if requested)
            if result.success and config.setup_domain and config.cloudflare_token:
                # Get actual host port from successful deployment URL
                # URL format: http://ip:port
                actual_host_port = None
                if successful:
                    try:
                        url = successful[0].url
                        if url and ':' in url:
                            actual_host_port = int(url.split(':')[-1])
                    except:
                        pass
                
                domain_result = await self._setup_domain(
                    config=config,
                    container_name=container_name,
                    server_ips=successful_ips,
                    actual_host_port=actual_host_port,
                    sidecar_port=internal_port,  # Stable port for zero-downtime vhost
                )
                if domain_result:
                    result.domain = domain_result.domain
                    result.domain_aliases = domain_result.aliases
            
            # Step 6: If this is a stateful service, find and restart dependent containers
            if result.success and config.is_stateful:
                restarted = await self._restart_dependent_containers(
                    config, ready_servers, container_name
                )
                if restarted:
                    result.restarted_dependents = restarted
            
            # Summary
            self.log(f"{'═' * 50}")
            self.log(f"✅ Deployment Complete: {len(successful)}/{total} successful")
            if successful:
                self.log(f"📋 Access:")
                if result.domain:
                    self.log(f"   🌐 https://{result.domain}")
                for s in successful:
                    self.log(f"   • {s.url}")
                if internal_port:
                    self.log(f"🔀 Service mesh: nginx:{internal_port} → {config.name}")
                
                # Architecture visualization
                self.log(f"")
                self.log(f"📐 Architecture:")
                if result.domain:
                    self.log(f"   ☁️  Cloudflare DNS: {result.domain}")
                    self.log(f"   │")
                    for i, s in enumerate(successful):
                        port = s.url.split(':')[-1] if s.url else '?'
                        prefix = "├" if i < len(successful) - 1 else "└"
                        self.log(f"   {prefix}── 🖥️  {s.ip}:443 (nginx vhost)")
                        self.log(f"   {'│' if i < len(successful) - 1 else ' '}       └── 🐳 {container_name}:{port}")
                else:
                    for i, s in enumerate(successful):
                        port = s.url.split(':')[-1] if s.url else '?'
                        self.log(f"   🖥️  {s.ip}:{port} → 🐳 {container_name}")
            
            return result
            
        except Exception as e:
            self.log(f"❌ Error: {e}")
            return MultiDeployResult(success=False, error=str(e))
    
    # =========================================================================
    # Domain Setup
    # =========================================================================
    
    async def _setup_domain(
        self,
        config: MultiDeployConfig,
        container_name: str,
        server_ips: List[str],
        actual_host_port: Optional[int] = None,
        sidecar_port: Optional[int] = None,
    ):
        """
        Set up auto-generated domain for deployment.
        
        Creates:
        - Cloudflare DNS A records pointing to all server IPs
        - Nginx virtual host on each server
        
        Args:
            config: Deployment config
            container_name: Full container name
            server_ips: List of successful server IPs
            actual_host_port: Actual host port from toggle deployment (overrides config)
            sidecar_port: Stable internal port (nginx sidecar) - use for zero-downtime
            
        Returns:
            DomainResult or None if failed
        """
        from ..networking.domains import DomainService
        
        try:
            domain_svc = DomainService(
                cloudflare_token=config.cloudflare_token,
                base_domain=config.base_domain,
                log=self.log,
            )
            
            # Determine container port - prefer actual_host_port from toggle deployment
            if actual_host_port:
                container_port = actual_host_port
            elif config.source_type == DeploySource.IMAGE_FILE and config.container_port:
                container_port = config.container_port
            else:
                container_port = config.port
            
            # Provision domain with sidecar_port for zero-downtime
            result = await domain_svc.provision_domain(
                container_name=container_name,
                server_ips=server_ips,
                container_port=container_port,
                agent_client_factory=self._agent,
                proxied=True,
                sidecar_port=sidecar_port,  # Stable port for vhost config
            )
            
            # Add aliases if specified
            if result.success and config.domain_aliases:
                for alias in config.domain_aliases:
                    alias_result = await domain_svc.add_domain_alias(
                        primary_domain=result.domain,
                        alias_domain=alias,
                        server_ips=server_ips,
                        container_name=container_name,
                        container_port=container_port,
                        agent_client_factory=self._agent,
                        create_dns=alias.endswith(config.base_domain),  # Only create DNS for our domains
                    )
                    if alias_result.success:
                        result.aliases.append(alias)
            
            return result
            
        except Exception as e:
            self.log(f"⚠️ Domain setup failed: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    # =========================================================================
    # Dependent Container Restart (when stateful services are deployed)
    # =========================================================================
    
    async def _restart_dependent_containers(
        self,
        config: MultiDeployConfig,
        servers: List[Dict[str, str]],
        new_service_container: str,
    ) -> List[str]:
        """
        Find and restart containers that depend on the newly deployed stateful service.
        
        When deploying postgres/redis/etc, we need to restart app containers so they
        pick up the new DATABASE_URL/REDIS_URL env vars.
        
        Args:
            config: The deployment config (for project/env info)
            servers: List of servers to check
            new_service_container: Name of the newly deployed container
            
        Returns:
            List of container names that were restarted
        """
        from ..utils.naming import DeploymentNaming
        from .env_builder import DeployEnvBuilder, is_stateful_service
        
        project_name = config.project or config.name
        workspace_id = config.workspace_id or "default"
        
        # Build prefix to match: {workspace}_{project}_{env}_
        container_prefix = f"{workspace_id}_{project_name}_{config.environment}_"
        
        self.log(f"🔄 Checking for dependent containers to restart...")
        
        restarted = []
        
        for server in servers:
            ip = server["ip"]
            agent = self._agent(ip)
            
            try:
                # List all containers
                result = await agent.list_containers()
                if not result.success:
                    continue
                
                containers = result.data.get("containers", [])
                
                for container in containers:
                    name = container.get("Names", container.get("name", ""))
                    state = container.get("State", container.get("status", ""))
                    
                    # Skip non-running containers
                    if state.lower() != "running":
                        continue
                    
                    # Skip the container we just deployed
                    if name == new_service_container:
                        continue
                    
                    # Skip if not in same project/env
                    if not name.startswith(container_prefix):
                        continue
                    
                    # Parse service name from container name
                    parts = name.split("_")
                    if len(parts) < 4:
                        continue
                    
                    service_name = "_".join(parts[3:])
                    
                    # Skip other stateful services
                    if is_stateful_service(service_name):
                        continue
                    
                    # This is an app container in the same project/env - restart it
                    self.log(f"   🔄 Restarting {name} to inject new env vars...")
                    
                    try:
                        # Get container's current config via inspect
                        inspect_result = await agent.inspect_container(name)
                        if not inspect_result.success:
                            self.log(f"      ⚠️ Failed to inspect {name}")
                            continue
                        
                        inspect_data = inspect_result.data
                        
                        # Extract original config
                        image = inspect_data.get("Config", {}).get("Image", "")
                        original_env = inspect_data.get("Config", {}).get("Env", [])
                        
                        # Parse port mappings
                        port_bindings = inspect_data.get("HostConfig", {}).get("PortBindings", {})
                        ports = {}
                        for container_port, bindings in port_bindings.items():
                            if bindings:
                                host_port = bindings[0].get("HostPort", "")
                                # container_port is like "8000/tcp"
                                cp = container_port.split("/")[0]
                                if host_port:
                                    ports[host_port] = cp
                        
                        # Parse volumes
                        mounts = inspect_data.get("Mounts", [])
                        volumes = []
                        for mount in mounts:
                            if mount.get("Type") == "bind":
                                src = mount.get("Source", "")
                                dst = mount.get("Destination", "")
                                if src and dst:
                                    volumes.append(f"{src}:{dst}")
                        
                        # Build new env vars with updated service discovery
                        env_builder = DeployEnvBuilder(
                            user=workspace_id,
                            project=project_name,
                            env=config.environment,
                            service=service_name,
                        )
                        
                        # Add the new dependency
                        env_builder.add_dependency(config.name)
                        
                        # Merge with original env (keeping user-defined vars)
                        new_env_dict = env_builder.build_env_vars()
                        
                        # Parse original env into dict
                        original_env_dict = {}
                        for e in original_env:
                            if "=" in e:
                                k, v = e.split("=", 1)
                                original_env_dict[k] = v
                        
                        # Merge: new env vars override, but keep others
                        merged_env = {**original_env_dict, **new_env_dict}
                        
                        # Stop and remove old container
                        await agent.stop_container(name)
                        await agent.remove_container(name)
                        
                        # Start with updated env
                        run_result = await agent.run_container(
                            name=name,
                            image=image,
                            ports=ports,
                            env_vars=merged_env,
                            volumes=volumes if volumes else None,
                            restart_policy="unless-stopped",
                            replace_existing=False,
                        )
                        
                        if run_result.success:
                            self.log(f"      ✅ Restarted {name}")
                            restarted.append(name)
                        else:
                            self.log(f"      ❌ Failed to restart {name}: {run_result.error}")
                            
                    except Exception as e:
                        self.log(f"      ⚠️ Error restarting {name}: {e}")
                        
            except Exception as e:
                self.log(f"   ⚠️ Error checking server {ip}: {e}")
        
        if restarted:
            self.log(f"🔄 Restarted {len(restarted)} dependent container(s)")
        else:
            self.log(f"   No dependent containers found")
        
        return restarted
    
    # =========================================================================
    # Server Collection
    # =========================================================================
    
    async def _collect_servers(self, config: MultiDeployConfig) -> List[Dict[str, str]]:
        """Collect existing servers and provision new ones."""
        servers = []
        
        # Add existing servers
        for ip in config.server_ips:
            servers.append({"ip": ip, "name": f"existing-{ip}"})
        
        if config.server_ips:
            self.log(f"📡 {len(config.server_ips)} existing server{'s' if len(config.server_ips) > 1 else ''}")
        
        # Provision new servers
        if config.new_server_count > 0:
            if not config.snapshot_id:
                self.log("⚠️ Cannot provision: no snapshot selected")
            else:
                self.log(f"🆕 Provisioning {config.new_server_count} new...")
                new_servers = await self._provision_servers(config)
                servers.extend(new_servers)
        
        return servers
    
    async def _provision_servers(self, config: MultiDeployConfig) -> List[Dict[str, str]]:
        """Provision new servers from snapshot (truly parallel)."""
        from ..utils.naming import generate_friendly_name
        
        servers = []
        names = [generate_friendly_name() for _ in range(config.new_server_count)]
        
        # Step 1: Start all droplets at once (don't wait)
        droplet_ids = []
        for name in names:
            try:
                droplet = self.do_client.create_droplet(
                    name=name,
                    region=config.region,
                    size=config.size,
                    image=config.snapshot_id,
                    tags=["deployed-via-api"],
                    wait=False,  # Don't block - just start creation
                )
                droplet_ids.append((droplet.id, name))
                self.log(f"   🔄 {name} creating...")
            except Exception as e:
                self.log(f"   ❌ {name}: {e}")
        
        if not droplet_ids:
            return servers
        
        # Step 2: Poll all in parallel until ready
        self.log(f"   ⏳ Waiting for {len(droplet_ids)} droplet{'s' if len(droplet_ids) > 1 else ''}...")
        
        async def wait_for_droplet(droplet_id: int, name: str):
            for _ in range(120):  # 4 min timeout
                try:
                    droplet = self.do_client.get_droplet(droplet_id)
                    if droplet and droplet.status == "active" and droplet.ip:
                        self.log(f"   ✅ {name} ({droplet.ip})")
                        return {"ip": droplet.ip, "name": name}
                except:
                    pass
                await asyncio.sleep(2)
            self.log(f"   ❌ {name}: timeout")
            return None
        
        tasks = [wait_for_droplet(did, name) for did, name in droplet_ids]
        results = await asyncio.gather(*tasks)
        
        for r in results:
            if r:
                servers.append(r)
        
        return servers
    
    # =========================================================================
    # Agent Health
    # =========================================================================
    
    async def _wait_for_agents(
        self,
        servers: List[Dict[str, str]],
        timeout: int = 120,
        interval: int = 2,
    ) -> List[Dict[str, str]]:
        """Wait for node agents to be ready."""
        ready = []
        
        async def check_one(server: Dict[str, str]) -> bool:
            ip = server["ip"]
            agent = self._agent(ip)
            
            for attempt in range(timeout // interval):
                try:
                    result = await agent.ping()
                    if result.success:
                        version = result.data.get("version", "?")
                        self.log(f"   ✅ {server['name']} agent ready (v{version})")
                        return True
                except:
                    pass
                
                if attempt < timeout // interval - 1:
                    await asyncio.sleep(interval)
            
            self.log(f"   ❌ {server['name']} agent timeout")
            return False
        
        tasks = [check_one(s) for s in servers]
        results = await asyncio.gather(*tasks)
        
        for server, is_ready in zip(servers, results):
            if is_ready:
                ready.append(server)
        
        return ready
    
    # =========================================================================
    # Dockerfile (for git)
    # =========================================================================
    
    async def _get_dockerfile_from_git(
        self,
        config: MultiDeployConfig,
        server: Dict[str, str],
    ) -> Optional[str]:
        """Clone repo on first server and get/generate Dockerfile."""
        ip = server["ip"]
        agent = self._agent(ip)
        
        self.log(f"📦 Cloning {config.git_url} ({config.git_branch})...")
        
        clone_result = await agent.git_clone(
            repo_url=config.git_url,
            branch=config.git_branch,
            target_path="/app/",
            access_token=config.git_token,
        )
        
        if not clone_result.success:
            self.log(f"   ❌ Clone failed: {clone_result.error}")
            return None
        
        self.log(f"   ✅ Cloned (commit: {clone_result.data.get('commit', '?')[:8]})")
        
        # Get Dockerfile
        self.log(f"📄 Fetching Dockerfile...")
        df_result = await agent.get_dockerfile("/app/")
        
        if not df_result.success:
            self.log(f"   ❌ Failed: {df_result.error}")
            return None
        
        dockerfile = df_result.data.get("dockerfile", "")
        source = df_result.data.get("source", "unknown")
        self.log(f"   ✅ Dockerfile ({source})")
        
        return dockerfile
    
    # =========================================================================
    # Deploy to Servers
    # =========================================================================
    
    async def _deploy_to_servers(
        self,
        config: MultiDeployConfig,
        servers: List[Dict[str, str]],
        dockerfile: Optional[str] = None,
    ) -> tuple[List[ServerResult], str, Optional[int]]:
        """
        Deploy to all servers in parallel with zero-downtime toggle support.
        
        Toggle deployment pattern:
        1. Check which container (primary/secondary) is currently running
        2. Deploy to the OTHER container name/port
        3. Health check new container
        4. Update nginx to point to new container
        5. Remove old container
        
        Returns:
            Tuple of (results, container_name, internal_port)
        """
        from ..utils.naming import DeploymentNaming, PortResolver
        from ..networking.service import NginxService
        from ..networking.ports import DeploymentPortResolver
        
        project_name = config.project or config.name
        workspace_id = config.workspace_id or "default"
        
        # Get BASE container name (without _secondary)
        base_container_name = DeploymentNaming.get_container_name(
            workspace_id=workspace_id,
            project=project_name,
            env=config.environment,
            service_name=config.name,
            secondary=False,
        )
        
        # Pre-calculate internal port for sidecar (same for all servers, STABLE)
        internal_port = None
        if config.setup_sidecar:
            internal_port = DeploymentPortResolver.get_internal_port(
                workspace_id, project_name, config.environment, config.name
            )
        
        # Calculate base host port for toggle
        base_host_port = PortResolver.get_host_port(
            workspace_id, project_name, config.environment, config.name,
            str(config.port)
        )
        
        async def deploy_one(server: Dict[str, str], is_first: bool) -> ServerResult:
            ip = server["ip"]
            name = server["name"]
            agent = self._agent(ip)
            sidecar_configured = False
            old_container_name = None
            
            try:
                # Step 0: Determine toggle - check what's running
                toggle_result = await self._determine_toggle(
                    agent, base_container_name, base_host_port
                )
                new_container_name = toggle_result["name"]
                new_host_port = toggle_result["port"]
                old_container_name = toggle_result.get("old_name")
                is_first_deploy = toggle_result["is_first"]
                
                if is_first_deploy:
                    self.log(f"🔄 [{name}] First deployment → {new_container_name}")
                else:
                    self.log(f"🔄 [{name}] Toggle: {old_container_name} → {new_container_name}")
                
                # Step 0b: CRITICAL - Remove target container if it exists (crashed/stopped)
                # This handles the case where previous deploy failed and left a dead container
                try:
                    target_status = await agent.container_status(new_container_name)
                    if target_status.success:
                        # Container exists (running, stopped, or crashed) - remove it first
                        self.log(f"   [{name}] Removing existing {new_container_name}...")
                        await agent.stop_container(new_container_name)
                        await agent.remove_container(new_container_name)
                except Exception:
                    pass  # Container doesn't exist, that's fine
                
                # Step 1: Prepare code/image
                if config.source_type == DeploySource.CODE:
                    self.log(f"   [{name}] Uploading code...")
                    tar_data = self._ensure_tar(config.code_tar)
                    result = await agent.upload_tar(tar_data, "/app/")
                    if not result.success:
                        raise Exception(f"Upload failed: {result.error}")
                        
                elif config.source_type == DeploySource.GIT:
                    # First server already cloned during dockerfile fetch
                    if not is_first:
                        self.log(f"   [{name}] Cloning repo...")
                        result = await agent.git_clone(
                            repo_url=config.git_url,
                            branch=config.git_branch,
                            target_path="/app/",
                            access_token=config.git_token,
                        )
                        if not result.success:
                            raise Exception(f"Clone failed: {result.error}")
                
                elif config.source_type == DeploySource.IMAGE_FILE:
                    size_mb = len(config.image_tar) / 1024 / 1024
                    self.log(f"   [{name}] Uploading {size_mb:.1f}MB to droplet...")
                    import time
                    upload_start = time.time()
                    result = await agent.load_image(config.image_tar)
                    upload_time = time.time() - upload_start
                    if not result.success:
                        raise Exception(f"Load failed: {result.error}")
                    self.log(f"   [{name}] Image loaded ({upload_time:.1f}s)")
                
                # Step 2: Build (for code/git) or Pull (for IMAGE)
                if config.source_type in (DeploySource.CODE, DeploySource.GIT):
                    self.log(f"   [{name}] Building image...")
                    result = await agent.build_image(
                        context_path="/app/",
                        image_tag=f"{config.name}:latest",
                        dockerfile=dockerfile,
                    )
                    if not result.success:
                        raise Exception(f"Build failed: {result.error}")
                    
                    image = f"{config.name}:latest"
                elif config.source_type == DeploySource.IMAGE:
                    self.log(f"   [{name}] Pulling image...")
                    result = await agent.pull_image(config.image)
                    if not result.success and "up to date" not in str(result.error):
                        raise Exception(f"Pull failed: {result.error}")
                    image = config.image
                else:
                    # IMAGE_FILE - already loaded
                    image = config.image
                
                # Step 3: Determine port mapping
                if config.source_type == DeploySource.IMAGE_FILE and config.container_port and config.host_port:
                    # For IMAGE_FILE, use configured ports but apply toggle offset if secondary
                    if DeploymentNaming.is_secondary_container(new_container_name):
                        expose_port = config.host_port + PortResolver.SECONDARY_PORT_OFFSET
                    else:
                        expose_port = config.host_port
                    container_port = config.container_port
                    port_mapping = {str(expose_port): str(container_port)}
                else:
                    # Use toggle port
                    expose_port = new_host_port
                    container_port = config.port
                    port_mapping = {str(expose_port): str(container_port)}
                
                # Step 4: Run NEW container (old one still running for zero-downtime)
                self.log(f"   [{name}] Starting container {new_container_name}...")
                
                result = await agent.run_container(
                    name=new_container_name,
                    image=image,
                    ports=port_mapping,
                    env_vars=config.env_vars,
                )
                if not result.success:
                    raise Exception(f"Run failed: {result.error}")
                
                url = f"http://{ip}:{expose_port}"
                self.log(f"   [{name}] ✅ Container started at {url}")
                
                # Step 5: Health check - wait for NEW container to be ready
                # This is CRITICAL for zero-downtime: don't switch nginx until confirmed healthy
                health_url = f"{url}/health"
                max_attempts = 30  # 30 seconds max wait
                healthy = False
                
                self.log(f"   [{name}] ⏳ Waiting for health check...")
                for attempt in range(max_attempts):
                    try:
                        # Try common health endpoints
                        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
                            # Try /health first, then /api/health, then root
                            for endpoint in ['/health', '/api/health', '/api/v1/health', '/']:
                                try:
                                    check_url = f"http://{ip}:{expose_port}{endpoint}"
                                    async with session.get(check_url) as resp:
                                        if resp.status < 500:  # 2xx, 3xx, 4xx all mean "app is responding"
                                            healthy = True
                                            self.log(f"   [{name}] ✅ Health check passed ({endpoint})")
                                            break
                                except:
                                    continue
                        if healthy:
                            break
                    except Exception as e:
                        pass
                    
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(1)
                
                if not healthy:
                    # Container not healthy - abort and keep old container running
                    self.log(f"   [{name}] ❌ Health check failed after {max_attempts}s - aborting switch")
                    # Stop the unhealthy new container
                    try:
                        await agent.stop_container(new_container_name)
                        await agent.remove_container(new_container_name)
                    except:
                        pass
                    raise Exception(f"Health check failed - new container not responding")
                
                # Step 6: Setup sidecar to point to NEW container (safe - we confirmed it's healthy)
                if config.setup_sidecar:
                    try:
                        self.log(f"   [{name}] Setting up nginx sidecar...")
                        nginx = NginxService(agent, log=self.log)
                        
                        # Quick check if nginx already running (skip full ensure for toggle deploys)
                        status = await agent.container_status("nginx")
                        nginx_running = status.success and status.data.get("status", "").lower() == "running"
                        
                        if not nginx_running:
                            nginx_result = await nginx.ensure_running()
                            if not nginx_result.success:
                                self.log(f"   [{name}] ⚠️ Failed to start nginx: {nginx_result.error}")
                        
                        # Point nginx to NEW container port (this is the critical switch)
                        sidecar_result = await nginx.setup_service_sidecar(
                            user_id=workspace_id,
                            project=project_name,
                            environment=config.environment,
                            service=config.name,
                            container_name=new_container_name,
                            container_port=expose_port,  # NEW port
                            is_stateful=config.is_stateful,
                            mode="multi_server",
                            backends=[{"ip": ip, "port": expose_port}],
                        )
                        
                        if sidecar_result.success:
                            sidecar_configured = True
                            self.log(f"   [{name}] 🔀 Sidecar: nginx:{internal_port} → {ip}:{expose_port}")
                        else:
                            self.log(f"   [{name}] ⚠️ Sidecar config failed: {sidecar_result.error}")
                    except Exception as se:
                        import traceback
                        self.log(f"   [{name}] ⚠️ Sidecar error: {se}")
                        traceback.print_exc()
                
                # Step 7: Remove OLD container (after nginx switch)
                if old_container_name:
                    try:
                        self.log(f"   [{name}] Cleaning up old container {old_container_name}...")
                        await agent.stop_container(old_container_name)
                        await agent.remove_container(old_container_name)
                        self.log(f"   [{name}] ✅ Removed {old_container_name}")
                    except Exception as cleanup_err:
                        self.log(f"   [{name}] ⚠️ Cleanup warning: {cleanup_err}")
                
                return ServerResult(
                    ip=ip, 
                    name=name, 
                    success=True, 
                    url=url,
                    internal_port=internal_port,
                    sidecar_configured=sidecar_configured,
                )
                
            except Exception as e:
                self.log(f"   [{name}] ❌ {e}")
                return ServerResult(ip=ip, name=name, success=False, error=str(e))
        
        # Deploy in parallel
        tasks = [deploy_one(s, i == 0) for i, s in enumerate(servers)]
        results = await asyncio.gather(*tasks)
        
        # Return base container name (clients use this to identify the service)
        return list(results), base_container_name, internal_port
    
    async def _determine_toggle(
        self,
        agent: NodeAgentClient,
        base_container_name: str,
        base_host_port: int,
    ) -> Dict[str, Any]:
        """
        Determine which container/port to use based on what's running.
        
        Toggle logic:
        - If nothing RUNNING → use base (port 8xxx, name "base")
        - If base RUNNING → use secondary (port 18xxx, name "base_secondary")
        - If secondary RUNNING → use base (port 8xxx, name "base")
        
        Note: We only consider RUNNING containers. Stopped/crashed containers
        are cleaned up before deploying (in deploy_one).
        
        Returns:
            {
                "name": container name to deploy,
                "port": host port to use,
                "old_name": container to remove after (or None),
                "is_first": True if first deployment
            }
        """
        from ..utils.naming import DeploymentNaming, PortResolver
        
        secondary_container_name = DeploymentNaming.get_secondary_container_name(base_container_name)
        secondary_port = PortResolver.get_secondary_port(base_host_port)
        
        # Check which container is RUNNING (not just exists)
        primary_running = await self._is_container_running(agent, base_container_name)
        secondary_running = await self._is_container_running(agent, secondary_container_name)
        
        if not primary_running and not secondary_running:
            # Nothing running - use base
            return {
                "name": base_container_name,
                "port": base_host_port,
                "old_name": None,
                "is_first": True,
            }
        elif primary_running:
            # Primary running - deploy to secondary
            return {
                "name": secondary_container_name,
                "port": secondary_port,
                "old_name": base_container_name,
                "is_first": False,
            }
        else:
            # Secondary running - deploy to primary
            return {
                "name": base_container_name,
                "port": base_host_port,
                "old_name": secondary_container_name,
                "is_first": False,
            }
    
    async def _is_container_running(self, agent: NodeAgentClient, container_name: str) -> bool:
        """Check if a container is running (not just exists)."""
        try:
            status = await agent.container_status(container_name)
            return (
                status.success and 
                status.data.get("status", "").lower() == "running"
            )
        except Exception:
            return False
    
    async def find_running_container(
        self,
        agent: NodeAgentClient,
        workspace_id: str,
        project: str,
        env: str,
        service: str,
    ) -> Optional[str]:
        """
        Find which container (primary or secondary) is running for a service.
        
        Args:
            agent: NodeAgentClient for the server
            workspace_id: Workspace ID
            project: Project name
            env: Environment
            service: Service name
            
        Returns:
            Container name if found running, None otherwise
        """
        from ..utils.naming import DeploymentNaming
        
        base_name = DeploymentNaming.get_container_name(
            workspace_id, project, env, service, secondary=False
        )
        secondary_name = DeploymentNaming.get_secondary_container_name(base_name)
        
        for name in [base_name, secondary_name]:
            if await self._is_container_running(agent, name):
                return name
        
        return None
    
    async def find_service_container(
        self,
        agent: NodeAgentClient,
        workspace_id: str,
        project: str,
        env: str,
        service: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Find container for a service (running or stopped).
        
        Returns info about the container that exists, preferring running over stopped.
        
        Returns:
            {"name": str, "status": str, "port": int} or None
        """
        from ..utils.naming import DeploymentNaming, PortResolver
        
        base_name = DeploymentNaming.get_container_name(
            workspace_id, project, env, service, secondary=False
        )
        secondary_name = DeploymentNaming.get_secondary_container_name(base_name)
        base_port = PortResolver.get_host_port(workspace_id, project, env, service)
        secondary_port = PortResolver.get_secondary_port(base_port)
        
        containers = [
            (base_name, base_port),
            (secondary_name, secondary_port),
        ]
        
        # Check for running first
        for name, port in containers:
            try:
                status = await agent.container_status(name)
                if status.success and status.data.get("status", "").lower() == "running":
                    return {"name": name, "status": "running", "port": port}
            except Exception:
                pass
        
        # Then check for any existing
        for name, port in containers:
            try:
                status = await agent.container_status(name)
                if status.success:
                    return {
                        "name": name, 
                        "status": status.data.get("status", "unknown"),
                        "port": port
                    }
            except Exception:
                pass
        
        return None


# Convenience function
async def deploy(
    config: MultiDeployConfig,
    do_token: str,
    agent_key: str = "hostomatic-agent-key",
    log: LogCallback = None,
) -> MultiDeployResult:
    """Deploy with a single function call."""
    service = DeploymentService(do_token, agent_key, log)
    return await service.deploy(config)
