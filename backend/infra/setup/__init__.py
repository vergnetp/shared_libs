"""
Setup Service - Environment initialization and configuration.

Handles:
- Base snapshot creation
- Region transfers
- Initial configuration

Can be used from FastAPI (streaming) or CLI (sync).
"""

from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Iterator, Callable
import time

from ..cloud import SnapshotService, SnapshotConfig, SNAPSHOT_PRESETS
from ..cloud import generate_node_agent_key
from ..node_agent import AGENT_VERSION


@dataclass
class SetupEvent:
    """Event from setup operations."""
    type: str  # log, progress, done, error
    data: Dict[str, Any] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = {"type": self.type}
        if self.data:
            result.update(self.data)
        return result


class SetupService:
    """
    Environment setup and initialization service.
    
    Usage (streaming - FastAPI):
        service = SetupService(do_token, user_id)
        for event in service.init_environment():
            yield event.to_dict()
    
    Usage (sync - CLI):
        service = SetupService(do_token, user_id)
        result = service.init_environment_sync()
    """
    
    ALL_REGIONS = [
        "nyc1", "nyc3", "ams3", "sfo2", "sfo3", 
        "sgp1", "lon1", "fra1", "tor1", "blr1", "syd1"
    ]
    
    def __init__(
        self,
        do_token: str,
        user_id: str,
        log: Callable[[str], None] = None,
    ):
        self.do_token = do_token
        self.user_id = user_id
        self.api_key = generate_node_agent_key(do_token)
        self._log = log or (lambda msg: None)
        self.snapshot_service = SnapshotService(do_token)
    
    def _event(self, type: str, **data) -> SetupEvent:
        return SetupEvent(type=type, data=data if data else None)
    
    def _log_event(self, msg: str) -> SetupEvent:
        self._log(msg)
        return self._event("log", message=msg)
    
    def _progress_event(self, pct: int) -> SetupEvent:
        return self._event("progress", percent=pct)
    
    # =========================================================================
    # Init Environment (Streaming)
    # =========================================================================
    
    def init_environment(
        self,
        region: str = "lon1",
        cleanup_on_failure: bool = False,
    ) -> Iterator[SetupEvent]:
        """
        Initialize environment: create base snapshot and transfer to all regions.
        
        Yields events for streaming responses.
        
        Args:
            region: Region to create snapshot in
            cleanup_on_failure: Whether to cleanup on failure
            
        Yields:
            SetupEvent objects with progress/log/done events
        """
        yield self._log_event("🚀 Starting environment setup...")
        yield self._progress_event(5)
        
        try:
            # Check if base snapshot already exists
            snapshots = self.snapshot_service.list_snapshots()
            base_snapshot = self._find_base_snapshot(snapshots)
            
            if base_snapshot:
                snap_name = base_snapshot.get("name")
                yield self._log_event(f"✅ Base snapshot already exists: {snap_name}")
                snapshot_id = base_snapshot.get("id")
            else:
                # Create base snapshot
                yield self._log_event("📦 Creating base snapshot (this takes 5-10 minutes)...")
                yield self._progress_event(10)
                
                snapshot_id = None
                for event in self._create_base_snapshot(region, cleanup_on_failure):
                    yield event
                    if event.type == "done":
                        data = event.data or {}
                        if data.get("success"):
                            snapshot_id = data.get("snapshot_id")
                        else:
                            raise Exception(data.get("error", "Snapshot creation failed"))
                
                if not snapshot_id:
                    raise Exception("No snapshot ID returned")
            
            yield self._progress_event(60)
            
            # Transfer to all regions
            yield from self._transfer_to_all_regions(snapshot_id)
            
            yield self._progress_event(100)
            yield self._event("done", success=True, snapshot_id=snapshot_id)
            
        except Exception as e:
            yield self._event("error", message=str(e))
            yield self._event("done", success=False, error=str(e))
    
    def _find_base_snapshot(self, snapshots: List[Dict]) -> Optional[Dict]:
        """Find existing base snapshot."""
        for s in snapshots:
            name = s.get("name", "")
            if name.startswith("base-") or name == "base-docker-ubuntu":
                return s
        return None
    
    def _create_base_snapshot(
        self,
        region: str,
        cleanup_on_failure: bool,
    ) -> Iterator[SetupEvent]:
        """Create base snapshot with agent installed."""
        preset = SNAPSHOT_PRESETS.get("base", SNAPSHOT_PRESETS["minimal"])
        preset_config = preset["config"]
        
        snapshot_name = f"base-agent-v{AGENT_VERSION.replace('.', '-')}"
        
        config = SnapshotConfig(
            name=snapshot_name,
            install_docker=preset_config.install_docker,
            apt_packages=preset_config.apt_packages,
            pip_packages=preset_config.pip_packages,
            docker_images=preset_config.docker_images,
            node_agent_api_key=self.api_key,
        )
        
        for event in self.snapshot_service.ensure_snapshot_stream(
            config, 
            region=region, 
            cleanup_on_failure=cleanup_on_failure
        ):
            # Convert snapshot service events to setup events
            yield SetupEvent(
                type=event.get("type", "log"),
                data=event.get("data") if "data" in event else {k: v for k, v in event.items() if k != "type"},
            )
    
    def _transfer_to_all_regions(self, snapshot_id: str) -> Iterator[SetupEvent]:
        """Transfer snapshot to all regions."""
        snapshots = self.snapshot_service.list_snapshots()
        current_snapshot = next(
            (s for s in snapshots if str(s.get("id")) == str(snapshot_id)), 
            None
        )
        current_regions = current_snapshot.get("regions", []) if current_snapshot else []
        
        missing_regions = [r for r in self.ALL_REGIONS if r not in current_regions]
        
        if not missing_regions:
            yield self._log_event("✅ Snapshot already available in all regions")
            return
        
        yield self._log_event(f"🌍 Transferring to {len(missing_regions)} regions...")
        
        # Retry transfer with delays
        for attempt in range(3):
            time.sleep(5)
            
            try:
                result = self.snapshot_service.transfer_snapshot_to_all_regions(
                    snapshot_id, 
                    wait=False
                )
                transferring = len(result.get("transferring_to", []))
                yield self._log_event(f"✅ Transfer initiated to {transferring} regions (runs in background)")
                return
            except Exception as e:
                if attempt < 2:
                    yield self._log_event(f"⏳ Waiting for snapshot to be available (attempt {attempt + 1}/3)...")
                else:
                    yield self._log_event(f"⚠️ Transfer failed after 3 attempts: {str(e)}")
                    yield self._log_event("💡 You can manually transfer via 'All Regions' button later")
    
    # =========================================================================
    # Init Environment (Sync - for CLI)
    # =========================================================================
    
    def init_environment_sync(
        self,
        region: str = "lon1",
        cleanup_on_failure: bool = False,
    ) -> Dict[str, Any]:
        """
        Synchronous version for CLI usage.
        
        Returns:
            Dict with success, snapshot_id, error
        """
        result = {"success": False}
        
        for event in self.init_environment(region, cleanup_on_failure):
            if event.type == "log":
                print(event.data.get("message", ""))
            elif event.type == "progress":
                pass  # Could show progress bar
            elif event.type == "done":
                result = event.data or {}
        
        return result
    
    # =========================================================================
    # Status Check
    # =========================================================================
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get current setup status.
        
        Returns:
            Dict with base_snapshot, regions_available, ready
        """
        snapshots = self.snapshot_service.list_snapshots()
        base_snapshot = self._find_base_snapshot(snapshots)
        
        if not base_snapshot:
            return {
                "ready": False,
                "base_snapshot": None,
                "regions_available": [],
                "message": "No base snapshot found. Run init first.",
            }
        
        regions = base_snapshot.get("regions", [])
        
        return {
            "ready": len(regions) > 0,
            "base_snapshot": {
                "id": base_snapshot.get("id"),
                "name": base_snapshot.get("name"),
            },
            "regions_available": regions,
            "all_regions": len(regions) >= len(self.ALL_REGIONS),
            "message": f"Ready in {len(regions)} regions",
        }


# Background task for queue-based streaming
def setup_init_task(entity: Dict[str, Any]) -> Dict[str, Any]:
    """
    Setup initialization task for job_queue workers.
    
    Uses StreamContext to emit events.
    """
    from shared_libs.backend.streaming import StreamContext
    
    ctx = StreamContext.from_dict(entity.get("stream_ctx", {}))
    do_token = entity.get("do_token")
    user_id = entity.get("user_id")
    region = entity.get("region", "lon1")
    
    def log_callback(msg: str):
        ctx.log(msg)
    
    service = SetupService(do_token, user_id, log=log_callback)
    
    try:
        result = None
        for event in service.init_environment(region=region):
            if event.type == "log":
                ctx.log(event.data.get("message", ""))
            elif event.type == "progress":
                ctx.progress(event.data.get("percent", 0))
            elif event.type == "done":
                result = event.data or {}
        
        if result and result.get("success"):
            ctx.complete(success=True, snapshot_id=result.get("snapshot_id"))
        else:
            ctx.complete(success=False, error=result.get("error") if result else "Unknown error")
        
        return result or {"success": False}
        
    except Exception as e:
        ctx.complete(success=False, error=str(e))
        return {"success": False, "error": str(e)}
