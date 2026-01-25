"""
Deployment Orchestrator - Direct async streaming for deployments.

Architecture (direct streaming - no Redis needed):
    ┌─────────────┐   async for    ┌─────────────┐    await    ┌─────────────┐
    │   FastAPI   │──────────────>│  Orchestrator│───────────>│  Deploy     │
    │   Route     │<──────────────│  (generator) │<───────────│  Service    │
    └──────┬──────┘   yield event └─────────────┘   callback  └─────────────┘
           │
           │ StreamingResponse
           v
    ┌─────────────┐
    │   Client    │
    │   (SSE)     │
    └─────────────┘

Usage in route:
    from shared_libs.backend.infra.deploy.orchestrator import deploy_with_streaming, DeployJobConfig
    
    @router.post("/deploy")
    async def deploy(req: DeployRequest, ...):
        config = DeployJobConfig(...)
        
        async def generate():
            async for event in deploy_with_streaming(config):
                yield f"data: {json.dumps(event)}\n\n"
        
        return StreamingResponse(generate(), media_type="text/event-stream")

This is the same pattern used by provisioning and snapshot creation.
No Redis, no workers, just direct async streaming.
"""

from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional, TYPE_CHECKING

from .service import DeploymentService, MultiDeployConfig, MultiDeployResult, DeploySource

if TYPE_CHECKING:
    from shared_libs.backend.streaming import StreamContext


# =============================================================================
# Deploy Config (serializable for job queue)
# =============================================================================

@dataclass
class DeployJobConfig:
    """
    Serializable deployment configuration for job queue.
    
    Flattened version of MultiDeployConfig for JSON serialization.
    
    deployment_type:
        - "service": Standard deployment (HTTP service with domain)
        - "worker": Background worker (no domain)
        - "snapshot": Create snapshot from deploy (temp server, then snapshot)
    """
    # Required
    name: str
    workspace_id: str
    do_token: str
    
    # Deployment type
    deployment_type: str = "service"  # service, worker, snapshot
    snapshot_name: Optional[str] = None  # For snapshot type: name for the snapshot
    
    # Project context
    project: Optional[str] = None
    environment: str = "prod"
    
    # Source
    source_type: str = "image"
    image: Optional[str] = None
    git_url: Optional[str] = None
    git_branch: str = "main"
    git_token: Optional[str] = None
    git_folders: Optional[List[Dict[str, Any]]] = None
    dockerfile: Optional[str] = None
    code_tar_b64: Optional[str] = None
    image_tar_b64: Optional[str] = None
    exclude_patterns: Optional[List[str]] = None
    
    # Infrastructure
    server_ips: List[str] = field(default_factory=list)
    new_server_count: int = 0
    snapshot_id: Optional[str] = None
    region: str = "lon1"
    size: str = "s-1vcpu-1gb"
    
    # Container
    port: int = 8000
    container_port: Optional[int] = None
    host_port: Optional[int] = None
    env_vars: Dict[str, str] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    
    # Service mesh
    depends_on: List[str] = field(default_factory=list)
    setup_sidecar: bool = True
    is_stateful: bool = False
    
    # Domain
    setup_domain: bool = False
    cloudflare_token: Optional[str] = None
    base_domain: str = "digitalpixo.com"
    domain_aliases: List[str] = field(default_factory=list)
    
    # Meta
    deployment_id: Optional[str] = None
    comment: Optional[str] = None
    deployed_by: Optional[str] = None
    
    # Rollback
    skip_pull: bool = False
    is_rollback: bool = False
    rollback_from_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DeployJobConfig':
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in valid_fields})
    
    def to_multi_config(self) -> MultiDeployConfig:
        """Convert to MultiDeployConfig for DeploymentService."""
        import base64
        
        config = MultiDeployConfig(
            name=self.name,
            port=self.port,
            container_port=self.container_port,
            host_port=self.host_port,
            env_vars=self.env_vars,
            environment=self.environment,
            tags=self.tags,
            source_type=DeploySource.from_value(self.source_type),
            git_url=self.git_url,
            git_branch=self.git_branch,
            git_token=self.git_token,
            git_folders=self.git_folders,
            image=self.image,
            skip_pull=self.skip_pull,
            server_ips=self.server_ips,
            new_server_count=self.new_server_count,
            snapshot_id=self.snapshot_id,
            region=self.region,
            size=self.size,
            dockerfile=self.dockerfile,
            project=self.project,
            workspace_id=self.workspace_id,
            deployment_id=self.deployment_id,
            depends_on=self.depends_on,
            setup_sidecar=self.setup_sidecar,
            is_stateful=self.is_stateful,
            setup_domain=self.setup_domain,
            cloudflare_token=self.cloudflare_token,
            base_domain=self.base_domain,
            domain_aliases=self.domain_aliases,
        )
        
        if self.code_tar_b64:
            config.code_tar = base64.b64decode(self.code_tar_b64)
        if self.image_tar_b64:
            config.image_tar = base64.b64decode(self.image_tar_b64)
        
        return config


# =============================================================================
# Background Tasks
# =============================================================================

def deploy_task(entity: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deploy task for job_queue workers.
    
    Emits events via StreamContext → Redis Pub/Sub → SSE.
    """
    from shared_libs.backend.streaming import StreamContext
    
    start_time = time.time()
    
    ctx = StreamContext.from_dict(entity.get("stream_ctx", {}))
    config = DeployJobConfig.from_dict(entity.get("config", {}))
    
    ctx.log(f"🚀 Starting deployment: {config.name}")
    ctx.log(f"📦 Source: {config.source_type}")
    ctx.log(f"🌍 Environment: {config.environment}")
    if config.server_ips:
        ctx.log(f"🖥️ Servers: {', '.join(config.server_ips)}")
    
    try:
        result = _run_async(_deploy_async(ctx, config))
        duration = time.time() - start_time
        
        if result.success:
            ctx.log(f"✅ Deployment complete in {duration:.1f}s")
            ctx.complete(
                success=True,
                deployment_id=config.deployment_id,
                duration_seconds=duration,
                servers=[s.to_dict() for s in result.servers] if result.servers else [],
                container_name=result.container_name,
                internal_port=result.internal_port,
            )
        else:
            ctx.log(f"❌ Deployment failed: {result.error}")
            ctx.complete(
                success=False,
                error=result.error,
                deployment_id=config.deployment_id,
                duration_seconds=duration,
            )
        
        return {"success": result.success, "error": result.error}
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        duration = time.time() - start_time
        error_msg = str(e)
        ctx.log(f"❌ Deployment error: {error_msg}")
        ctx.complete(success=False, error=error_msg, duration_seconds=duration)
        return {"success": False, "error": error_msg}


def rollback_task(entity: Dict[str, Any]) -> Dict[str, Any]:
    """Rollback task - deploys a previous image version."""
    from shared_libs.backend.streaming import StreamContext
    
    start_time = time.time()
    
    ctx = StreamContext.from_dict(entity.get("stream_ctx", {}))
    config = DeployJobConfig.from_dict(entity.get("config", {}))
    
    config.skip_pull = True
    config.is_rollback = True
    
    ctx.log(f"🔄 Starting rollback: {config.name}")
    ctx.log(f"📌 Target image: {config.image}")
    
    try:
        result = _run_async(_deploy_async(ctx, config))
        duration = time.time() - start_time
        
        if result.success:
            ctx.log(f"✅ Rollback complete in {duration:.1f}s")
            ctx.complete(
                success=True,
                deployment_id=config.deployment_id,
                duration_seconds=duration,
                rolled_back_to=config.image,
            )
        else:
            ctx.complete(
                success=False,
                error=result.error,
                deployment_id=config.deployment_id,
                duration_seconds=duration,
            )
        
        return {"success": result.success, "error": result.error}
        
    except Exception as e:
        duration = time.time() - start_time
        ctx.complete(success=False, error=str(e), duration_seconds=duration)
        return {"success": False, "error": str(e)}


def stateful_deploy_task(entity: Dict[str, Any]) -> Dict[str, Any]:
    """Deploy stateful service (postgres, redis, etc)."""
    from shared_libs.backend.streaming import StreamContext
    
    start_time = time.time()
    
    ctx = StreamContext.from_dict(entity.get("stream_ctx", {}))
    config = DeployJobConfig.from_dict(entity.get("config", {}))
    
    config.is_stateful = True
    config.skip_pull = True  # Images pre-loaded in snapshot
    
    ctx.log(f"🗄️ Deploying stateful service: {config.name}")
    ctx.log(f"📦 Image: {config.image}")
    
    try:
        result = _run_async(_deploy_async(ctx, config))
        duration = time.time() - start_time
        
        if result.success:
            ctx.log(f"✅ Stateful service ready in {duration:.1f}s")
            ctx.complete(
                success=True,
                deployment_id=config.deployment_id,
                duration_seconds=duration,
                service_type=config.name,
                servers=[s.to_dict() for s in result.servers] if result.servers else [],
                container_name=result.container_name,
                internal_port=result.internal_port,
            )
        else:
            ctx.complete(success=False, error=result.error, duration_seconds=duration)
        
        return {"success": result.success, "error": result.error}
        
    except Exception as e:
        duration = time.time() - start_time
        ctx.complete(success=False, error=str(e), duration_seconds=duration)
        return {"success": False, "error": str(e)}
        return {"success": False, "error": str(e)}


# =============================================================================
# Async Deployment
# =============================================================================

async def _deploy_async(ctx, config: DeployJobConfig) -> MultiDeployResult:
    """Run deployment using DeploymentService."""
    
    def log_callback(msg: str):
        ctx.log(msg)
    
    service = DeploymentService(
        do_token=config.do_token,
        log=log_callback,
        user_id=config.deployed_by,
    )
    
    return await service.deploy(config.to_multi_config())


def _run_async(coro):
    """Run async code in sync context (for job_queue workers)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# =============================================================================
# Direct Streaming (no Redis/workers needed)
# =============================================================================

@dataclass
class StreamEvent:
    """Event emitted during deployment streaming."""
    type: str  # "log", "progress", "error", "complete"
    message: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    timestamp: Optional[str] = None
    
    def __post_init__(self):
        if self.timestamp is None:
            from datetime import datetime
            self.timestamp = datetime.utcnow().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        result = {"type": self.type, "timestamp": self.timestamp}
        if self.message:
            result["message"] = self.message
        if self.data:
            result.update(self.data)
        return result


async def deploy_with_streaming(config: DeployJobConfig):
    """
    Deploy with direct async streaming.
    
    Yields StreamEvent objects. No Redis/workers needed.
    
    Usage:
        async for event in deploy_with_streaming(config):
            yield f"data: {json.dumps(event.to_dict())}\\n\\n"
    """
    start_time = time.time()
    event_queue: asyncio.Queue = asyncio.Queue()
    
    def emit(event_type: str, message: str = None, **data):
        """Put event in queue (called from sync context)."""
        event = StreamEvent(type=event_type, message=message, data=data if data else None)
        # Use call_soon_threadsafe if needed, but we're in same thread
        try:
            event_queue.put_nowait(event)
        except Exception:
            pass  # Queue full, skip event
    
    def log_callback(msg: str):
        emit("log", msg)
    
    # Emit initial events
    emit("log", f"🚀 Starting deployment: {config.name}")
    emit("log", f"📦 Source: {config.source_type}")
    emit("log", f"🌍 Environment: {config.environment}")
    if config.server_ips:
        emit("log", f"🖥️ Servers: {', '.join(config.server_ips)}")
    
    # Yield initial events
    while not event_queue.empty():
        yield await event_queue.get()
    
    # Run deployment
    try:
        def emit_event_callback(event_type: str, message: str, data: Dict):
            """Callback for DeployService to emit events (e.g., server_provisioned)."""
            emit(event_type, message, **data)
        
        service = DeploymentService(
            do_token=config.do_token,
            log=log_callback,
            user_id=config.deployed_by,
            emit_event=emit_event_callback,
        )
        
        # Start deploy as task so we can yield events during execution
        deploy_task = asyncio.create_task(service.deploy(config.to_multi_config()))
        
        # Poll for events while deploy runs
        while not deploy_task.done():
            # Yield any pending events
            while not event_queue.empty():
                yield await event_queue.get()
            
            # Small sleep to not busy-wait
            await asyncio.sleep(0.1)
        
        # Get result
        result = await deploy_task
        duration = time.time() - start_time
        
        # Yield any remaining events
        while not event_queue.empty():
            yield await event_queue.get()
        
        # Emit completion
        if result.success:
            emit("log", f"✅ Deployment complete in {duration:.1f}s")
            # Yield final log before done
            while not event_queue.empty():
                yield await event_queue.get()
            
            # Build done event data
            done_data = {
                "success": True,
                "deployment_id": config.deployment_id,
                "duration_seconds": duration,
                "servers": [s.to_dict() for s in result.servers] if result.servers else [],
                "container_name": result.container_name,
                "internal_port": result.internal_port,
            }
            
            # Include domain if set up
            if result.domain:
                done_data["domain"] = result.domain
            if result.domain_aliases:
                done_data["domain_aliases"] = result.domain_aliases
            
            yield StreamEvent(
                type="done",
                message="Deployment successful",
                data=done_data,
            )
        else:
            emit("log", f"❌ Deployment failed: {result.error}")
            # Yield final log before done
            while not event_queue.empty():
                yield await event_queue.get()
            yield StreamEvent(
                type="done",
                message="Deployment failed",
                data={
                    "success": False,
                    "error": result.error,
                    "deployment_id": config.deployment_id,
                    "duration_seconds": duration,
                },
            )
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        duration = time.time() - start_time
        yield StreamEvent(
            type="done",
            message=f"Deployment error: {e}",
            data={
                "success": False,
                "error": str(e),
                "deployment_id": config.deployment_id,
                "duration_seconds": duration,
            },
        )


async def rollback_with_streaming(config: DeployJobConfig):
    """
    Rollback with direct async streaming.
    
    Same pattern as deploy_with_streaming but for rollbacks.
    """
    config.skip_pull = True
    config.is_rollback = True
    
    start_time = time.time()
    event_queue: asyncio.Queue = asyncio.Queue()
    
    def emit(event_type: str, message: str = None, **data):
        event = StreamEvent(type=event_type, message=message, data=data if data else None)
        try:
            event_queue.put_nowait(event)
        except Exception:
            pass
    
    def log_callback(msg: str):
        emit("log", msg)
    
    emit("log", f"🔄 Starting rollback: {config.name}")
    emit("log", f"📌 Target image: {config.image}")
    
    while not event_queue.empty():
        yield await event_queue.get()
    
    try:
        service = DeploymentService(
            do_token=config.do_token,
            log=log_callback,
            user_id=config.deployed_by,
        )
        
        deploy_task = asyncio.create_task(service.deploy(config.to_multi_config()))
        
        while not deploy_task.done():
            while not event_queue.empty():
                yield await event_queue.get()
            await asyncio.sleep(0.1)
        
        result = await deploy_task
        duration = time.time() - start_time
        
        while not event_queue.empty():
            yield await event_queue.get()
        
        if result.success:
            emit("log", f"✅ Rollback complete in {duration:.1f}s")
            # Yield final log before done
            while not event_queue.empty():
                yield await event_queue.get()
            yield StreamEvent(
                type="done",
                message="Rollback successful",
                data={
                    "success": True,
                    "deployment_id": config.deployment_id,
                    "duration_seconds": duration,
                    "rolled_back_to": config.image,
                },
            )
        else:
            yield StreamEvent(
                type="done",
                message="Rollback failed",
                data={
                    "success": False,
                    "error": result.error,
                    "deployment_id": config.deployment_id,
                    "duration_seconds": duration,
                },
            )
            
    except Exception as e:
        duration = time.time() - start_time
        yield StreamEvent(
            type="done",
            message=f"Rollback error: {e}",
            data={
                "success": False,
                "error": str(e),
                "deployment_id": config.deployment_id,
                "duration_seconds": duration,
            },
        )


async def stateful_deploy_with_streaming(config: DeployJobConfig):
    """
    Deploy stateful service with direct async streaming.
    """
    config.is_stateful = True
    config.skip_pull = True  # Images pre-loaded in snapshot
    
    start_time = time.time()
    event_queue: asyncio.Queue = asyncio.Queue()
    
    def emit(event_type: str, message: str = None, **data):
        event = StreamEvent(type=event_type, message=message, data=data if data else None)
        try:
            event_queue.put_nowait(event)
        except Exception:
            pass
    
    def log_callback(msg: str):
        emit("log", msg)
    
    emit("log", f"🗄️ Deploying stateful service: {config.name}")
    emit("log", f"📦 Image: {config.image}")
    
    while not event_queue.empty():
        yield await event_queue.get()
    
    try:
        def emit_event_callback(event_type: str, message: str, data: Dict):
            """Callback for DeployService to emit events (e.g., server_provisioned)."""
            emit(event_type, message, **data)
        
        service = DeploymentService(
            do_token=config.do_token,
            log=log_callback,
            user_id=config.deployed_by,
            emit_event=emit_event_callback,
        )
        
        deploy_task = asyncio.create_task(service.deploy(config.to_multi_config()))
        
        while not deploy_task.done():
            while not event_queue.empty():
                yield await event_queue.get()
            await asyncio.sleep(0.1)
        
        result = await deploy_task
        duration = time.time() - start_time
        
        while not event_queue.empty():
            yield await event_queue.get()
        
        if result.success:
            # Build connection URL using DeployEnvBuilder (same logic as service mesh)
            from .env_builder import DeployEnvBuilder, detect_stateful_service_type
            
            server_ip = config.server_ips[0] if config.server_ips else "localhost"
            port = result.internal_port or config.port
            
            # Use env builder to get deterministic credentials
            env_builder = DeployEnvBuilder(
                user=config.workspace_id,
                project=config.project or "default",
                env=config.environment,
                service=config.name,
            )
            
            # Use robust detection based on name and image
            service_type = detect_stateful_service_type(
                config.name, 
                config.image, 
                is_stateful=True
            )
            
            if service_type == "redis":
                password = env_builder._get_service_password("redis")
                connection_url = f"redis://:{password}@{server_ip}:{port}/0"
                env_var_name = "REDIS_URL"
            elif service_type in ("postgres", "postgresql"):
                db_user = env_builder._get_db_user("postgres")
                db_password = env_builder._get_service_password("postgres")
                db_name = env_builder._get_db_name("postgres")
                connection_url = f"postgresql://{db_user}:{db_password}@{server_ip}:{port}/{db_name}"
                env_var_name = "DATABASE_URL"
            elif service_type in ("mysql", "mariadb"):
                db_user = env_builder._get_db_user("mysql")
                db_password = env_builder._get_service_password("mysql")
                db_name = env_builder._get_db_name("mysql")
                connection_url = f"mysql://{db_user}:{db_password}@{server_ip}:{port}/{db_name}"
                env_var_name = "MYSQL_URL"
            elif service_type in ("mongo", "mongodb"):
                db_user = env_builder._get_db_user("mongo")
                db_password = env_builder._get_service_password("mongo")
                db_name = env_builder._get_db_name("mongo")
                connection_url = f"mongodb://{db_user}:{db_password}@{server_ip}:{port}/{db_name}"
                env_var_name = "MONGO_URL"
            else:
                connection_url = f"{server_ip}:{port}"
                env_var_name = f"{config.name.upper()}_URL"
            
            emit("log", f"✅ Stateful service ready in {duration:.1f}s")
            # Mask password in log for security
            masked_url = connection_url
            if ":" in connection_url and "@" in connection_url:
                # redis://:password@host:port -> redis://:****@host:port
                # postgresql://user:password@host -> postgresql://user:****@host
                import re
                masked_url = re.sub(r'(://[^:]*:)[^@]+(@)', r'\1****\2', connection_url)
            emit("log", f"📡 Connection: {env_var_name}={masked_url}")
            
            # Yield any remaining buffered logs before done event
            while not event_queue.empty():
                yield await event_queue.get()
            
            yield StreamEvent(
                type="done",
                message="Stateful service deployed",
                data={
                    "success": True,
                    "deployment_id": config.deployment_id,
                    "duration_seconds": duration,
                    "service_type": config.name,
                    "servers": [s.to_dict() for s in result.servers] if result.servers else [],
                    "container_name": result.container_name,
                    "internal_port": result.internal_port,
                    "connection_url": connection_url,
                    "env_var_name": env_var_name,
                },
            )
        else:
            yield StreamEvent(
                type="done",
                message="Stateful deploy failed",
                data={
                    "success": False,
                    "error": result.error,
                    "deployment_id": config.deployment_id,
                    "duration_seconds": duration,
                },
            )
            
    except Exception as e:
        duration = time.time() - start_time
        yield StreamEvent(
            type="done",
            message=f"Stateful deploy error: {e}",
            data={
                "success": False,
                "error": str(e),
                "deployment_id": config.deployment_id,
                "duration_seconds": duration,
            },
        )


# =============================================================================
# Snapshot Creation via Deploy
# =============================================================================

async def create_snapshot_with_streaming(config: DeployJobConfig):
    """
    Create a snapshot by deploying to a temp server, then snapshotting.
    
    Flow:
    1. Provision temp droplet from base snapshot
    2. Deploy using existing flow (image/git/code → local/base:latest)
    3. Stop containers
    4. Power off droplet
    5. Create snapshot with config.snapshot_name
    6. Delete temp droplet
    7. Return snapshot info
    
    The resulting snapshot will have the deployed image pre-loaded as local/base:latest.
    User's Dockerfiles can then use FROM local/base:latest for fast deploys.
    
    Usage:
        config = DeployJobConfig(
            name="mybase",
            deployment_type="snapshot",
            snapshot_name="python-ml-optimized",
            source_type="image",
            image="myregistry/ml-deps:latest",
            ...
        )
        async for event in create_snapshot_with_streaming(config):
            yield f"data: {json.dumps(event.to_dict())}\\n\\n"
    """
    from ..providers.digitalocean import DOClient
    
    start_time = time.time()
    event_queue: asyncio.Queue = asyncio.Queue()
    temp_droplet_id = None
    
    def emit(event_type: str, message: str = None, **data):
        event = StreamEvent(type=event_type, message=message, data=data if data else None)
        try:
            event_queue.put_nowait(event)
        except Exception:
            pass
    
    def log_callback(msg: str):
        emit("log", msg)
    
    snapshot_name = config.snapshot_name or f"{config.name}-snapshot"
    
    # Initial events
    emit("log", f"🔨 Creating snapshot: {snapshot_name}")
    emit("log", f"📦 Source: {config.source_type}")
    emit("progress", percent=5)
    
    while not event_queue.empty():
        yield await event_queue.get()
    
    try:
        do_client = DOClient(config.do_token)
        
        # Step 1: Find base snapshot
        emit("log", "🔍 Finding base snapshot...")
        snapshots = do_client.list_snapshots()
        base_snapshot = None
        for snap in snapshots:
            if snap.get("name", "").startswith("base-docker-"):
                # Prefer same region
                regions = snap.get("regions", [])
                if config.region in regions:
                    base_snapshot = snap
                    break
                elif base_snapshot is None:
                    base_snapshot = snap
        
        if not base_snapshot:
            raise ValueError("No base snapshot found. Create one first with 'Manage Snapshot' tab.")
        
        emit("log", f"✅ Using base snapshot: {base_snapshot['name']}")
        emit("progress", percent=10)
        while not event_queue.empty():
            yield await event_queue.get()
        
        # Step 2: Provision temp droplet
        emit("log", "🖥️ Provisioning temporary server...")
        
        # Ensure snapshot is in the target region
        base_regions = base_snapshot.get("regions", [])
        if config.region not in base_regions:
            emit("log", f"📍 Transferring snapshot to {config.region}...")
            from ..providers.snapshot_service import SnapshotService
            snap_service = SnapshotService(config.do_token)
            snap_service._transfer_snapshot(base_snapshot["id"], config.region)
            emit("log", f"✅ Snapshot transferred to {config.region}")
        
        temp_name = f"snapshot-build-{int(time.time())}"
        droplet = do_client.create_droplet(
            name=temp_name,
            region=config.region,
            size=config.size or "s-2vcpu-4gb",  # Use larger size for builds
            image=base_snapshot["id"],
            tags=["snapshot-builder", "temporary"],
            wait=True,
            wait_timeout=300,
        )
        temp_droplet_id = droplet.id
        server_ip = droplet.public_ip
        
        emit("log", f"✅ Server ready: {server_ip}")
        emit("progress", percent=25)
        while not event_queue.empty():
            yield await event_queue.get()
        
        # Step 3: Wait for node agent
        emit("log", "⏳ Waiting for node agent...")
        from ..node_agent.client import NodeAgentClient
        agent = NodeAgentClient(server_ip, config.do_token, droplet_id=temp_droplet_id)
        
        max_wait = 120
        waited = 0
        while waited < max_wait:
            try:
                status = await agent.ping()
                if status.get("status") == "ok":
                    break
            except Exception:
                pass
            await asyncio.sleep(5)
            waited += 5
        
        if waited >= max_wait:
            raise TimeoutError("Node agent did not become ready in time")
        
        emit("log", "✅ Node agent ready")
        emit("progress", percent=30)
        while not event_queue.empty():
            yield await event_queue.get()
        
        # Step 4: Deploy to temp server (reuse existing deploy logic)
        emit("log", "🚀 Deploying to temporary server...")
        
        # Modify config for single server deploy
        deploy_config = DeployJobConfig.from_dict(config.to_dict())
        deploy_config.server_ips = [server_ip]
        deploy_config.new_server_count = 0
        deploy_config.setup_domain = False  # No domain for snapshot builds
        deploy_config.setup_sidecar = False  # No sidecar needed
        deploy_config.deployment_type = "service"  # Normal deploy
        
        service = DeploymentService(
            do_token=config.do_token,
            log=log_callback,
            user_id=config.deployed_by,
        )
        
        # Create task and poll for events
        deploy_task = asyncio.create_task(service.deploy(deploy_config.to_multi_config()))
        
        while not deploy_task.done():
            while not event_queue.empty():
                yield await event_queue.get()
            await asyncio.sleep(0.1)
        
        result = await deploy_task
        
        while not event_queue.empty():
            yield await event_queue.get()
        
        if not result.success:
            raise RuntimeError(f"Deploy failed: {result.error}")
        
        emit("log", "✅ Deploy complete")
        emit("progress", percent=60)
        while not event_queue.empty():
            yield await event_queue.get()
        
        # Step 5: Tag the deployed image as local/base:latest
        emit("log", "🏷️ Tagging image as local/base:latest...")
        
        # Find the container that was deployed
        containers = await agent.list_containers()
        deployed_container = None
        for c in containers:
            if config.name in c.get("name", ""):
                deployed_container = c
                break
        
        if deployed_container:
            image_name = deployed_container.get("image", "")
            if image_name and image_name != "local/base:latest":
                # Tag the image
                await agent.tag_image(image_name, "local/base:latest")
                emit("log", f"✅ Tagged {image_name} → local/base:latest")
        
        emit("progress", percent=65)
        while not event_queue.empty():
            yield await event_queue.get()
        
        # Step 6: Stop all containers
        emit("log", "⏹️ Stopping containers...")
        for c in containers:
            container_id = c.get("id")
            if container_id:
                try:
                    await agent.stop_container(container_id)
                except Exception:
                    pass  # Ignore errors stopping containers
        
        emit("log", "✅ Containers stopped")
        emit("progress", percent=70)
        while not event_queue.empty():
            yield await event_queue.get()
        
        # Step 7: Power off droplet
        emit("log", "🔌 Powering off server...")
        do_client.power_off_droplet(temp_droplet_id)
        
        # Wait for power off
        max_wait = 60
        waited = 0
        while waited < max_wait:
            droplet_status = do_client.get_droplet(temp_droplet_id)
            if droplet_status and droplet_status.status == "off":
                break
            await asyncio.sleep(5)
            waited += 5
        
        emit("log", "✅ Server powered off")
        emit("progress", percent=80)
        while not event_queue.empty():
            yield await event_queue.get()
        
        # Step 8: Create snapshot
        emit("log", f"📸 Creating snapshot: {snapshot_name}...")
        snapshot_result = do_client.create_snapshot_from_droplet(
            droplet_id=temp_droplet_id,
            name=snapshot_name,
            wait=True,
            wait_timeout=600,
        )
        
        snapshot_id = snapshot_result.get("id")
        emit("log", f"✅ Snapshot created: {snapshot_id}")
        emit("progress", percent=95)
        while not event_queue.empty():
            yield await event_queue.get()
        
        # Step 9: Delete temp droplet
        emit("log", "🗑️ Cleaning up temporary server...")
        do_client.delete_droplet(temp_droplet_id, force=True)
        temp_droplet_id = None  # Mark as deleted
        
        emit("log", "✅ Cleanup complete")
        emit("progress", percent=100)
        
        duration = time.time() - start_time
        
        while not event_queue.empty():
            yield await event_queue.get()
        
        yield StreamEvent(
            type="done",
            message="Snapshot created successfully",
            data={
                "success": True,
                "snapshot_id": snapshot_id,
                "snapshot_name": snapshot_name,
                "duration_seconds": duration,
                "base_image": "local/base:latest",
            },
        )
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        duration = time.time() - start_time
        
        # Cleanup on error
        if temp_droplet_id:
            try:
                emit("log", "🗑️ Cleaning up after error...")
                do_client = DOClient(config.do_token)
                do_client.delete_droplet(temp_droplet_id, force=True)
                emit("log", "✅ Temp server deleted")
            except Exception:
                emit("log", "⚠️ Failed to delete temp server - manual cleanup may be needed")
        
        while not event_queue.empty():
            yield await event_queue.get()
        
        yield StreamEvent(
            type="done",
            message=f"Snapshot creation failed: {e}",
            data={
                "success": False,
                "error": str(e),
                "duration_seconds": duration,
            },
        )


# Task for job queue
def create_snapshot_task(entity: Dict[str, Any]) -> Dict[str, Any]:
    """Create snapshot task for job_queue workers."""
    from shared_libs.backend.streaming import StreamContext
    
    start_time = time.time()
    ctx = StreamContext.from_dict(entity.get("stream_ctx", {}))
    config = DeployJobConfig.from_dict(entity.get("config", {}))
    
    ctx.log(f"🔨 Starting snapshot creation: {config.snapshot_name or config.name}")
    
    try:
        # Run the async generator synchronously
        async def run():
            result = {"success": False, "error": "No result"}
            async for event in create_snapshot_with_streaming(config):
                if event.message:
                    ctx.log(event.message)
                if event.type == "progress" and event.data:
                    ctx.progress(event.data.get("percent", 0))
                if event.type == "done":
                    result = event.data or {}
            return result
        
        result = _run_async(run())
        duration = time.time() - start_time
        
        if result.get("success"):
            ctx.complete(
                success=True,
                snapshot_id=result.get("snapshot_id"),
                snapshot_name=result.get("snapshot_name"),
                duration_seconds=duration,
            )
        else:
            ctx.complete(
                success=False,
                error=result.get("error"),
                duration_seconds=duration,
            )
        
        return result
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        duration = time.time() - start_time
        error_msg = str(e)
        ctx.log(f"❌ Snapshot creation error: {error_msg}")
        ctx.complete(success=False, error=error_msg, duration_seconds=duration)
        return {"success": False, "error": error_msg}


# =============================================================================
# Task Registry (kept for backward compatibility with workers)
# =============================================================================

DEPLOY_TASKS = {
    "deploy": deploy_task,
    "rollback": rollback_task,
    "stateful_deploy": stateful_deploy_task,
    "create_snapshot": create_snapshot_task,
}
