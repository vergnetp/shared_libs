"""
Deployment Service - Orchestrates multi-server deployments.

All deployment logic lives here. API is just a thin wrapper.
"""

from __future__ import annotations
import asyncio
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


@dataclass
class MultiDeployConfig:
    """Deployment configuration for multi-server deploys."""
    # App config
    name: str
    port: int = 8000
    container_port: Optional[int] = None  # For IMAGE_FILE: internal port
    host_port: Optional[int] = None       # For IMAGE_FILE: external port
    env_vars: Dict[str, str] = field(default_factory=dict)
    environment: str = "prod"  # prod/staging/dev/test/uat
    tags: List[str] = field(default_factory=list)
    
    # Source config
    source_type: DeploySource = DeploySource.CODE
    
    # For CODE source
    code_tar: Optional[bytes] = None
    dockerfile: Optional[str] = None
    
    # For GIT source
    git_url: Optional[str] = None
    git_branch: str = "main"
    git_token: Optional[str] = None
    
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


@dataclass
class ServerResult:
    """Result for a single server deployment."""
    ip: str
    name: str
    success: bool
    error: Optional[str] = None
    url: Optional[str] = None


@dataclass 
class MultiDeployResult:
    """Overall deployment result."""
    success: bool
    servers: List[ServerResult] = field(default_factory=list)
    successful_count: int = 0
    failed_count: int = 0
    error: Optional[str] = None
    
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
        
        # Detect zip by magic bytes (PK = 0x50 0x4B)
        if data[:2] == b'PK':
            zip_buffer = io.BytesIO(data)
            tar_buffer = io.BytesIO()
            
            with zipfile.ZipFile(zip_buffer, 'r') as zf:
                with tarfile.open(fileobj=tar_buffer, mode='w:gz') as tf:
                    for zip_info in zf.infolist():
                        name = zip_info.filename
                        tar_info = tarfile.TarInfo(name=name)
                        
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
            self.log(f"🚀 Deploying {config.name} to {total} server{'s' if total > 1 else ''} [{config.environment}]")
            
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
            
            # Step 4: Deploy to all servers
            self.log(f"🔨 Building and deploying...")
            results = await self._deploy_to_servers(config, ready_servers, dockerfile)
            
            successful = [r for r in results if r.success]
            failed = [r for r in results if not r.success]
            
            # Summary
            self.log(f"{'═' * 50}")
            self.log(f"✅ Deployment Complete: {len(successful)}/{total} successful")
            if successful:
                self.log(f"📋 Access:")
                for s in successful:
                    self.log(f"   • {s.url}")
            
            return MultiDeployResult(
                success=len(successful) > 0,
                servers=results,
                successful_count=len(successful),
                failed_count=len(failed),
            )
            
        except Exception as e:
            self.log(f"❌ Error: {e}")
            return MultiDeployResult(success=False, error=str(e))
    
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
    ) -> List[ServerResult]:
        """Deploy to all servers in parallel."""
        
        async def deploy_one(server: Dict[str, str], is_first: bool) -> ServerResult:
            ip = server["ip"]
            name = server["name"]
            agent = self._agent(ip)
            
            try:
                self.log(f"🔄 [{name}] Deploying...")
                
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
                
                # Step 3: Run container
                self.log(f"   [{name}] Starting container...")
                
                # Determine port mapping
                if config.source_type == DeploySource.IMAGE_FILE and config.container_port and config.host_port:
                    port_mapping = {str(config.host_port): str(config.container_port)}
                    expose_port = config.host_port
                else:
                    port_mapping = {str(config.port): str(config.port)}
                    expose_port = config.port
                
                result = await agent.run_container(
                    name=config.name,
                    image=image,
                    ports=port_mapping,
                    env_vars=config.env_vars,
                )
                if not result.success:
                    raise Exception(f"Run failed: {result.error}")
                
                url = f"http://{ip}:{expose_port}"
                self.log(f"   [{name}] ✅ Running at {url}")
                
                return ServerResult(ip=ip, name=name, success=True, url=url)
                
            except Exception as e:
                self.log(f"   [{name}] ❌ {e}")
                return ServerResult(ip=ip, name=name, success=False, error=str(e))
        
        # Deploy in parallel
        tasks = [deploy_one(s, i == 0) for i, s in enumerate(servers)]
        results = await asyncio.gather(*tasks)
        
        return list(results)


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
