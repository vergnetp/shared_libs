"""
Snapshot Service - Create and manage DO snapshots with Docker/node_agent pre-installed.

Uses DOClient from shared cloud module for all DO API calls.

This is the single source of truth for snapshot creation logic.
"""

import time
import subprocess
import secrets
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable, Generator

from .cloudinit import CloudInitConfig, build_cloudinit_script, SNAPSHOT_PRESETS, get_preset

# Use the new cloud/ module with connection pooling
from ...cloud.digitalocean import DOClient, AsyncDOClient, DOError, Result, MANAGED_TAG


@dataclass
class SnapshotResult:
    """Result of snapshot operation."""
    success: bool
    snapshot_id: Optional[str] = None
    snapshot_name: Optional[str] = None
    api_key: Optional[str] = None  # Node agent API key if installed
    created: bool = False  # True if newly created, False if already existed
    message: str = ""
    error: Optional[str] = None


@dataclass
class SnapshotConfig:
    """Configuration for snapshot creation."""
    name: str = "docker-ready-ubuntu-24"
    install_docker: bool = True
    apt_packages: List[str] = field(default_factory=list)
    pip_packages: List[str] = field(default_factory=list)
    docker_images: List[str] = field(default_factory=list)
    custom_commands: List[str] = field(default_factory=list)
    install_node_agent: bool = True
    node_agent_api_key: Optional[str] = None
    
    def to_cloudinit_config(self) -> CloudInitConfig:
        """Convert to CloudInitConfig."""
        return CloudInitConfig(
            install_docker=self.install_docker,
            apt_packages=self.apt_packages,
            pip_packages=self.pip_packages,
            docker_images=self.docker_images if self.install_docker else [],
            custom_commands=self.custom_commands,
            install_node_agent=self.install_node_agent,
            node_agent_api_key=self.node_agent_api_key,
        )


class SnapshotService:
    """
    Service for creating and managing DO snapshots.
    
    Uses DOClient from shared cloud module for all DO API calls.
    
    Usage:
        service = SnapshotService(do_token)
        
        # Simple check/create
        result = service.ensure_snapshot(config)
        
        # With progress streaming
        for event in service.ensure_snapshot_stream(config):
            print(event)
    """
    
    DEPLOYER_KEY_PATH = Path.home() / ".ssh" / "id_ed25519"
    
    def __init__(self, do_token: str):
        self.do_token = do_token
        self._client: Optional[DOClient] = None
    
    def _get_client(self) -> DOClient:
        """Get or create DO client."""
        if self._client is None:
            self._client = DOClient(self.do_token)
        return self._client
    
    @staticmethod
    def generate_api_key(do_token: str, user_id: str = "") -> str:
        """
        Generate deterministic API key using HMAC-SHA256.
        
        Uses DO token as the key and user_id as the message.
        HMAC prevents length extension attacks and guarantees uniqueness.
        
        Args:
            do_token: DigitalOcean API token (used as HMAC key)
            user_id: Optional user identifier (used as HMAC message)
            
        Returns:
            32-character hex string (first 32 chars of HMAC-SHA256)
            
        Note:
            Same inputs ALWAYS produce same output.
            User can regenerate anytime knowing their DO token + user_id.
        """
        import hashlib
        import hmac
        
        key = do_token.encode()
        msg = f"node-agent:{user_id}".encode()
        h = hmac.new(key, msg, hashlib.sha256)
        return h.hexdigest()[:32]
    
    # ==========================================
    # Public API
    # ==========================================
    
    def list_snapshots(self) -> List[Dict[str, Any]]:
        """List all droplet snapshots."""
        client = self._get_client()
        return client.list_snapshots()
    
    def get_snapshot_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Find snapshot by name."""
        client = self._get_client()
        return client.get_snapshot_by_name(name)
    
    def delete_snapshot(self, snapshot_id: str) -> bool:
        """Delete a snapshot."""
        client = self._get_client()
        result = client.delete_snapshot(snapshot_id)
        return result.success
    
    def transfer_snapshot_to_regions(self, snapshot_id: str, regions: List[str]) -> bool:
        """Transfer a snapshot to multiple regions."""
        if not regions:
            return True
        
        client = self._get_client()
        try:
            # Transfer to first region (DO handles multi-region)
            client.transfer_snapshot(snapshot_id, regions[0], wait=False)
            return True
        except Exception:
            return False
    
    def transfer_snapshot_to_all_regions(self, snapshot_id: str, wait: bool = False) -> dict:
        """Transfer a snapshot to all available DO regions.
        
        Args:
            snapshot_id: The snapshot ID to transfer
            wait: If True, wait for transfers to complete (can take 10+ minutes)
            
        Returns:
            Dict with 'success', 'transferring_to' list of regions
        """
        client = self._get_client()
        return client.transfer_snapshot_to_all_regions(snapshot_id, wait=wait)
    
    def ensure_snapshot(
        self,
        config: SnapshotConfig,
        region: str = "lon1",
        size: str = "s-1vcpu-1gb",
        force_recreate: bool = False,
        cleanup_on_failure: bool = True,
        remove_ssh_key: bool = True,
    ) -> SnapshotResult:
        """
        Ensure snapshot exists, create if needed.
        
        Blocking version - waits for completion.
        
        Args:
            cleanup_on_failure: If False, keeps droplet on failure for debugging
            remove_ssh_key: If True, removes deployer SSH key from DO after snapshot creation
        """
        # Collect all events, return final result
        result = None
        for event in self.ensure_snapshot_stream(config, region, size, force_recreate, cleanup_on_failure, remove_ssh_key):
            if event.get("type") == "done":
                data = event.get("data", {})
                return SnapshotResult(
                    success=data.get("success", False),
                    snapshot_id=data.get("snapshot_id"),
                    snapshot_name=data.get("snapshot_name"),
                    api_key=data.get("api_key"),
                    created=data.get("created", False),
                    message=data.get("message", ""),
                    error=data.get("error"),
                )
        
        return SnapshotResult(success=False, error="No result received")
    
    def ensure_snapshot_stream(
        self,
        config: SnapshotConfig,
        region: str = "lon1",
        size: str = "s-1vcpu-1gb",
        force_recreate: bool = False,
        cleanup_on_failure: bool = True,
        remove_ssh_key: bool = True,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Ensure snapshot exists with streaming progress.
        
        Args:
            cleanup_on_failure: If False, keeps droplet on failure for debugging
            remove_ssh_key: If True, removes deployer SSH key from DO after snapshot creation
                           (recommended for security - prevents SSH access to future droplets)
        
        Yields events:
            {"type": "log", "message": "..."}
            {"type": "progress", "step": 1, "total": 7, "description": "..."}
            {"type": "done", "data": {...}}
        """
        total_steps = 8
        
        def log(msg: str, step: int = None):
            yield {"type": "log", "message": msg}
            if step:
                yield {"type": "progress", "step": step, "total": total_steps, "description": msg}
        
        def done(success: bool, **kwargs):
            yield {"type": "done", "data": {"success": success, **kwargs}}
        
        try:
            # Step 1: Check existing
            yield from log("🔍 Checking for existing snapshot...", step=1)
            
            if not force_recreate:
                existing = self.get_snapshot_by_name(config.name)
                if existing:
                    yield from log(f"✅ Snapshot '{config.name}' already exists!")
                    yield from done(
                        success=True,
                        snapshot_id=existing["id"],
                        snapshot_name=existing["name"],
                        created=False,
                        message="Snapshot already exists",
                    )
                    return
            
            # Delete existing if force_recreate
            if force_recreate:
                yield from log("🗑️ Force recreate enabled, checking for existing...")
                existing = self.get_snapshot_by_name(config.name)
                if existing:
                    yield from log(f"🗑️ Deleting existing snapshot {existing['id']}...")
                    self.delete_snapshot(existing["id"])
                    yield from log("✅ Deleted existing snapshot")
            
            # Step 2: Setup SSH key
            yield from log("🔑 Setting up SSH key...", step=2)
            ssh_key_id = self._ensure_ssh_key()
            if not ssh_key_id:
                yield from done(success=False, error="Could not setup SSH key")
                return
            yield from log(f"✅ SSH key ready: {ssh_key_id}")
            
            # Step 3: Build cloud-init script
            yield from log("📝 Building cloud-init script...", step=3)
            
            cloudinit_config = config.to_cloudinit_config()
            
            # Use pre-set API key if provided, otherwise generate one
            if not cloudinit_config.node_agent_api_key:
                cloudinit_config.node_agent_api_key = self.generate_api_key(self.do_token)
                yield from log(f"  💡 API key auto-generated from DO token")
            else:
                yield from log(f"  💡 Using provided API key")
            
            messages = []
            user_data, api_key = build_cloudinit_script(
                cloudinit_config, 
                log=lambda msg: messages.append(msg)
            )
            for msg in messages:
                yield from log(msg)
            
            yield from log(f"  💡 API key derived from DO token (reproducible)")
            
            # Step 4: Create temp droplet
            yield from log(f"🖥️ Creating temporary droplet in {region}...", step=4)
            
            temp_name = f"snapshot-builder-{int(time.time())}"
            droplet_data, droplet_error = self._create_temp_droplet(
                name=temp_name,
                region=region,
                size=size,
                image="ubuntu-24-04-x64",
                ssh_key_id=ssh_key_id,
                user_data=user_data,
            )
            
            if not droplet_data:
                error_msg = f"Failed to create droplet: {droplet_error}" if droplet_error else "Failed to create droplet"
                yield from log(f"❌ {error_msg}")
                yield from done(success=False, error=error_msg)
                return
            
            droplet_id = droplet_data.get("id")
            yield from log(f"✅ Droplet created: {droplet_id}")
            
            # Wait for droplet to be active
            yield from log("⏳ Waiting for droplet to become active...")
            droplet_ip = self._wait_for_droplet_active(droplet_id)
            if not droplet_ip:
                if cleanup_on_failure:
                    self._delete_droplet(droplet_id)
                    yield from log("🗑️ Droplet deleted")
                else:
                    yield from log(f"⚠️ Droplet {droplet_id} kept for debugging (cleanup_on_failure=False)")
                yield from done(success=False, error="Droplet did not become active")
                return
            
            yield from log(f"✅ Droplet active at {droplet_ip}")
            
            # Step 5: Wait for setup to complete
            yield from log("⏳ Waiting for Docker/packages to install...", step=5)
            yield from log("  Checking via SSH for completion marker...")
            
            # Stream progress updates from wait loop
            setup_ok = False
            setup_msg = ""
            for event in self._wait_for_setup_stream(droplet_ip):
                if event.get("type") == "log":
                    yield event
                elif event.get("type") == "result":
                    setup_ok = event.get("success", False)
                    setup_msg = event.get("message", "")
            
            if not setup_ok:
                # Try to get debug logs and parse error
                yield from log("📋 Fetching cloud-init logs for debugging...")
                debug_logs = self._get_cloudinit_logs(droplet_ip)
                parsed_error = None
                
                if debug_logs:
                    yield from log(f"Cloud-init logs:\n{debug_logs[-1500:]}")
                    parsed_error = self._parse_error_from_logs(debug_logs)
                    if parsed_error:
                        yield from log(f"❌ Error detected: {parsed_error}")
                else:
                    yield from log("⚠️ Could not fetch cloud-init logs")
                
                if cleanup_on_failure:
                    self._delete_droplet(droplet_id)
                    yield from log("🗑️ Droplet deleted")
                else:
                    yield from log(f"⚠️ DROPLET KEPT FOR DEBUGGING: {droplet_ip} (ID: {droplet_id})")
                    yield from log(f"   SSH: ssh root@{droplet_ip}")
                    yield from log(f"   Logs: tail -f /var/log/cloud-init-output.log")
                
                # Use parsed error if available, otherwise generic message
                error_msg = parsed_error or setup_msg or "Setup did not complete"
                yield from done(success=False, error=error_msg, droplet_ip=droplet_ip if not cleanup_on_failure else None)
                return
            
            yield from log(f"✅ {setup_msg}")
            
            # Step 6: Verify node agent is running (if installed)
            if config.install_node_agent:
                yield from log("🔍 Verifying node agent...", step=6)
                agent_healthy = False
                agent_error = None
                
                # urllib is used for this simple internal ping check because:
                # - Single attempt to localhost, no retry/circuit breaker needed
                # - Shouldn't pollute external API call traces
                import urllib.request
                import urllib.error
                
                for attempt in range(3):
                    try:
                        req = urllib.request.Request(
                            f"http://{droplet_ip}:9999/ping",
                            headers={"User-Agent": "SnapshotService/1.0"}
                        )
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            data = json.loads(resp.read().decode())
                            version = data.get("version", "unknown")
                            yield from log(f"✅ Node agent v{version} responding")
                            agent_healthy = True
                            break
                    except urllib.error.URLError as e:
                        agent_error = f"Connection failed: {e.reason}"
                    except Exception as e:
                        agent_error = str(e)
                    
                    if attempt < 2:
                        time.sleep(5)
                
                if not agent_healthy:
                    # Agent failed - get detailed diagnostics via SSH
                    yield from log(f"❌ Node agent not responding: {agent_error}")
                    yield from log("📋 Collecting diagnostics...")
                    
                    def ssh_cmd(cmd: str, timeout: int = 30) -> str:
                        """Run SSH command and return output."""
                        try:
                            result = subprocess.run(
                                [
                                    "ssh",
                                    "-i", str(self.DEPLOYER_KEY_PATH),
                                    "-o", "StrictHostKeyChecking=no",
                                    "-o", "UserKnownHostsFile=/dev/null",
                                    "-o", "ConnectTimeout=10",
                                    "-o", "BatchMode=yes",
                                    f"root@{droplet_ip}",
                                    cmd
                                ],
                                capture_output=True,
                                text=True,
                                timeout=timeout,
                            )
                            return result.stdout.strip()
                        except Exception:
                            return ""
                    
                    try:
                        # Get systemctl status (shows if service is failing)
                        status = ssh_cmd("systemctl status node-agent --no-pager 2>&1 | head -15")
                        
                        # Get recent journal logs with python errors
                        journal = ssh_cmd("journalctl -u node-agent -n 30 --no-pager 2>&1")
                        
                        # Try running the agent directly to get clean error output
                        direct_error = ssh_cmd("cd /usr/local/bin && python3 -c 'import node_agent' 2>&1 || python3 node_agent.py 2>&1 | head -20")
                        
                        # Parse and display errors
                        error_found = False
                        
                        # Check for SyntaxError with details
                        if "SyntaxError" in journal or "SyntaxError" in direct_error:
                            error_found = True
                            yield from log("   💥 Python Syntax Error detected:")
                            # Extract the actual error from direct run (cleaner output)
                            error_source = direct_error if "SyntaxError" in direct_error else journal
                            lines = error_source.split('\n')
                            for i, line in enumerate(lines):
                                line = line.strip()
                                if 'File "' in line or 'SyntaxError' in line or line.startswith('^'):
                                    yield from log(f"      {line[:120]}")
                                elif 'line ' in line.lower() and i < len(lines) - 1:
                                    # Show the problematic line
                                    yield from log(f"      {line[:120]}")
                        
                        # Check for ModuleNotFoundError
                        elif "ModuleNotFoundError" in journal or "ModuleNotFoundError" in direct_error:
                            error_found = True
                            yield from log("   💥 Missing Python module:")
                            for line in (direct_error or journal).split('\n'):
                                if 'ModuleNotFoundError' in line or 'No module named' in line:
                                    yield from log(f"      {line.strip()[:120]}")
                        
                        # Check for ImportError
                        elif "ImportError" in journal or "ImportError" in direct_error:
                            error_found = True
                            yield from log("   💥 Import Error:")
                            for line in (direct_error or journal).split('\n'):
                                if 'ImportError' in line or 'cannot import' in line.lower():
                                    yield from log(f"      {line.strip()[:120]}")
                        
                        # Check for permission/path errors
                        elif "PermissionError" in journal or "FileNotFoundError" in journal:
                            error_found = True
                            yield from log("   💥 File/Permission Error:")
                            for line in journal.split('\n'):
                                if 'Error' in line:
                                    yield from log(f"      {line.strip()[:120]}")
                        
                        # Generic error - show service status and last error lines
                        if not error_found:
                            yield from log("   ⚠️ Service status:")
                            # Show relevant status lines
                            for line in status.split('\n'):
                                line = line.strip()
                                if line and ('Active:' in line or 'Process:' in line or 'status=' in line):
                                    yield from log(f"      {line[:120]}")
                            
                            # Show last few journal lines that look like errors
                            yield from log("   📝 Recent logs:")
                            shown = 0
                            for line in journal.split('\n')[-15:]:
                                line = line.strip()
                                # Skip systemd noise, show actual errors
                                if line and shown < 5:
                                    if any(x in line.lower() for x in ['error', 'failed', 'exception', 'traceback']):
                                        yield from log(f"      {line[:120]}")
                                        shown += 1
                                    elif 'python3' in line and 'systemd' not in line.lower():
                                        yield from log(f"      {line[:120]}")
                                        shown += 1
                        
                        # Check Flask installation
                        flask_check = ssh_cmd("python3 -c 'import flask; print(flask.__version__)' 2>&1")
                        if "ModuleNotFoundError" in flask_check or "No module" in flask_check:
                            yield from log("   🔧 Flask not installed - cloud-init may have failed")
                            
                            # Check cloud-init status for more details
                            cloud_init_status = ssh_cmd("cloud-init status 2>&1")
                            if cloud_init_status:
                                yield from log(f"   📋 Cloud-init status: {cloud_init_status[:100]}")
                            
                            # Check if cloud-init had errors
                            cloud_init_errors = ssh_cmd("grep -i 'error\\|failed\\|traceback' /var/log/cloud-init-output.log 2>/dev/null | tail -5")
                            if cloud_init_errors:
                                yield from log("   📋 Cloud-init errors:")
                                for line in cloud_init_errors.split('\n')[:3]:
                                    if line.strip():
                                        yield from log(f"      {line.strip()[:120]}")
                        
                        # Check if agent file exists
                        agent_exists = ssh_cmd("ls -la /usr/local/bin/node_agent.py 2>&1")
                        if "No such file" in agent_exists:
                            yield from log("   🔧 Agent script not found at /usr/local/bin/node_agent.py")
                            yield from log("   ℹ️  Cloud-init may not have completed - check base image compatibility")
                        
                    except Exception as e:
                        yield from log(f"   ⚠️ Could not fetch diagnostics: {e}")
                    
                    # Fail the snapshot - agent is broken
                    if cleanup_on_failure:
                        self._delete_droplet(droplet_id)
                        yield from log("🗑️ Droplet deleted")
                    else:
                        yield from log(f"⚠️ DROPLET KEPT FOR DEBUGGING: {droplet_ip} (ID: {droplet_id})")
                    
                    if remove_ssh_key and ssh_key_id:
                        self._delete_ssh_key(ssh_key_id)
                    
                    yield from done(success=False, error=f"Node agent failed to start: {agent_error}")
                    return
            
            # Step 7: Remove SSH key from droplet (BEFORE snapshot)
            if remove_ssh_key:
                yield from log("🔑 Removing SSH access from droplet...", step=7)
                try:
                    # Clear authorized_keys so snapshot doesn't include our key
                    # Use longer timeout and continue even on failure (we're powering off anyway)
                    result = subprocess.run(
                        [
                            "ssh",
                            "-i", str(self.DEPLOYER_KEY_PATH),
                            "-o", "StrictHostKeyChecking=no",
                            "-o", "UserKnownHostsFile=/dev/null",
                            "-o", "ConnectTimeout=15",
                            "-o", "BatchMode=yes",
                            "-o", "ServerAliveInterval=5",
                            f"root@{droplet_ip}",
                            "rm -f /root/.ssh/authorized_keys && echo 'SSH keys cleared'"
                        ],
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    if result.returncode == 0:
                        yield from log("✅ SSH keys cleared from droplet - snapshot will have no SSH access")
                    else:
                        yield from log(f"⚠️ Could not clear SSH keys (continuing anyway): {result.stderr[:100]}")
                except subprocess.TimeoutExpired:
                    yield from log("⚠️ SSH key clearing timed out (continuing anyway - keys will be in snapshot)")
                except Exception as e:
                    yield from log(f"⚠️ Failed to clear SSH keys (continuing anyway): {str(e)[:100]}")
            
            # Step 8: Create snapshot
            yield from log("📸 Powering off droplet for snapshot...", step=8)
            self._power_off_droplet(droplet_id)
            
            # Wait for power off
            for _ in range(30):
                status = self._get_droplet_status(droplet_id)
                if status == "off":
                    break
                time.sleep(2)
            
            yield from log(f"📸 Creating snapshot '{config.name}'...")
            snapshot_id = self._create_snapshot(droplet_id, config.name)
            
            if not snapshot_id:
                if cleanup_on_failure:
                    self._delete_droplet(droplet_id)
                    yield from log("🗑️ Droplet deleted")
                else:
                    yield from log(f"⚠️ DROPLET KEPT FOR DEBUGGING: {droplet_ip} (ID: {droplet_id})")
                yield from done(success=False, error="Failed to create snapshot")
                return
            
            yield from log(f"⏳ Waiting for snapshot to complete...")
            if not self._wait_for_snapshot(snapshot_id):
                if cleanup_on_failure:
                    self._delete_droplet(droplet_id)
                    yield from log("🗑️ Droplet deleted")
                else:
                    yield from log(f"⚠️ DROPLET KEPT FOR DEBUGGING: {droplet_ip} (ID: {droplet_id})")
                yield from done(success=False, error="Snapshot creation timed out")
                return
            
            yield from log(f"✅ Snapshot created: {snapshot_id}")
            
            # Step 8: Cleanup (always delete on success)
            yield from log("🧹 Cleaning up...", step=9)
            self._delete_droplet(droplet_id)
            yield from log("✅ Droplet deleted")
            
            # Remove SSH key from DO account (if not already done)
            if remove_ssh_key:
                if self._delete_ssh_key(ssh_key_id):
                    yield from log("✅ SSH key removed from DO account")
                # Note: SSH access already removed from snapshot in step 6
            else:
                yield from log("🔑 SSH key kept on DO (can SSH to new droplets)")
            
            yield from done(
                success=True,
                snapshot_id=snapshot_id,
                snapshot_name=config.name,
                api_key=api_key,
                created=True,
                message="Snapshot created successfully",
            )
            
        except Exception as e:
            yield from done(success=False, error=str(e))
    
    # ==========================================
    # Private helpers
    # ==========================================
    
    def _ensure_ssh_key(self) -> Optional[str]:
        """Ensure SSH key exists and is registered with DO."""
        private_key_path = self.DEPLOYER_KEY_PATH
        public_key_path = Path(str(private_key_path) + ".pub")
        
        # Generate if needed
        if not private_key_path.exists():
            private_key_path.parent.mkdir(mode=0o700, exist_ok=True)
            subprocess.run([
                "ssh-keygen", "-t", "ed25519",
                "-f", str(private_key_path),
                "-N", "", "-C", "deployer@infra"
            ], capture_output=True, check=True)
            private_key_path.chmod(0o600)
        
        if not public_key_path.exists():
            return None
        
        public_key = public_key_path.read_text().strip()
        
        client = self._get_client()
        
        # Check if already registered
        existing_keys = client.list_ssh_keys()
        for key in existing_keys:
            if key.get("public_key", "").strip() == public_key:
                return str(key["id"])
        
        # Upload new key
        try:
            new_key = client.add_ssh_key("deployer_key", public_key)
            return str(new_key.get("id"))
        except Exception:
            return None
    
    def _delete_ssh_key(self, key_id: str) -> bool:
        """Delete SSH key from DO account."""
        try:
            client = self._get_client()
            return client.delete_ssh_key(key_id)
        except Exception:
            return False
    
    def _create_temp_droplet(
        self,
        name: str,
        region: str,
        size: str,
        image: str,
        ssh_key_id: str,
        user_data: str = None,
    ) -> tuple:
        """
        Create temporary droplet for snapshot building.
        
        Returns:
            (droplet_data, error_message) - droplet_data is None on failure
        """
        payload = {
            "name": name,
            "region": region,
            "size": size,
            "image": image,
            "ssh_keys": [ssh_key_id],
            "tags": ["snapshot-builder", "temporary", MANAGED_TAG],
        }
        if user_data:
            payload["user_data"] = user_data
        
        try:
            client = self._get_client()
            result = client._post("/droplets", payload)
            return result.get("droplet", {}), None
        except Exception as e:
            return None, f"Request failed: {str(e)}"
    
    def _wait_for_droplet_active(self, droplet_id: int, timeout: int = 120) -> Optional[str]:
        """Wait for droplet to become active, return IP."""
        client = self._get_client()
        start = time.time()
        while time.time() - start < timeout:
            droplet = client.get_droplet(droplet_id)
            if droplet and droplet.is_active and droplet.ip:
                return droplet.ip
            time.sleep(5)
        return None
    
    def _get_droplet_status(self, droplet_id: int) -> Optional[str]:
        """Get droplet status."""
        client = self._get_client()
        droplet = client.get_droplet(droplet_id)
        return droplet.status if droplet else None
    
    def _wait_for_setup_stream(self, droplet_ip: str, timeout: int = 900) -> Generator[Dict[str, Any], None, None]:
        """
        Wait for cloud-init setup to complete via SSH.
        
        Yields:
            {"type": "log", "message": "..."} - Progress updates
            {"type": "result", "success": bool, "message": str} - Final result
        """
        private_key_path = self.DEPLOYER_KEY_PATH
        start = time.time()
        last_log = 0
        last_status_check = 0
        error_count = 0  # Track consecutive cloud-init errors
        
        while time.time() - start < timeout:
            elapsed = int(time.time() - start)
            
            # Log progress every 30 seconds
            if elapsed - last_log >= 30:
                yield {"type": "log", "message": f"  Checking setup... ({elapsed}s elapsed)"}
                last_log = elapsed
            
            # Every 60 seconds, do a detailed status check
            if elapsed - last_status_check >= 60:
                try:
                    status_result = subprocess.run(
                        [
                            "ssh", "-i", str(private_key_path),
                            "-o", "StrictHostKeyChecking=no",
                            "-o", "UserKnownHostsFile=/dev/null", 
                            "-o", "ConnectTimeout=10",
                            "-o", "BatchMode=yes",
                            f"root@{droplet_ip}",
                            "echo 'marker:' $(test -f /tmp/snapshot-setup-complete && echo YES || echo NO); "
                            "echo 'docker:' $(which docker 2>/dev/null || echo 'not found'); "
                            "echo 'cloud-init:' $(cloud-init status 2>/dev/null | head -1 || echo 'unknown')"
                        ],
                        capture_output=True, text=True, timeout=20,
                    )
                    if status_result.stdout.strip():
                        status_str = status_result.stdout.strip().replace(chr(10), ', ')
                        yield {"type": "log", "message": f"  Status: {status_str}"}
                        
                        # Check for cloud-init error - fail fast
                        if "cloud-init: status: error" in status_str:
                            error_count += 1
                            if error_count >= 2:  # Confirm error persists
                                yield {"type": "result", "success": False, "message": "cloud-init failed with error status"}
                                return
                        else:
                            error_count = 0
                            
                except Exception:
                    pass
                last_status_check = elapsed
            
            try:
                result = subprocess.run(
                    [
                        "ssh",
                        "-i", str(private_key_path),
                        "-o", "StrictHostKeyChecking=no",
                        "-o", "UserKnownHostsFile=/dev/null",
                        "-o", "ConnectTimeout=10",
                        "-o", "BatchMode=yes",
                        f"root@{droplet_ip}",
                        "test -f /tmp/snapshot-setup-complete && docker --version"
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                
                if result.returncode == 0:
                    msg = result.stdout.strip() or "Setup complete"
                    yield {"type": "result", "success": True, "message": msg}
                    return
                    
            except subprocess.TimeoutExpired:
                pass
            except Exception:
                pass
            
            time.sleep(10)
        
        yield {"type": "result", "success": False, "message": f"Setup did not complete in {timeout}s"}
    
    def _get_cloudinit_logs(self, droplet_ip: str) -> Optional[str]:
        """Get cloud-init logs for debugging - tries multiple sources."""
        
        # Try multiple log sources
        log_commands = [
            # Primary cloud-init log
            "tail -150 /var/log/cloud-init-output.log 2>/dev/null",
            # Cloud-init status
            "cloud-init status --long 2>/dev/null",
            # Syslog for cloud-init
            "grep -i cloud-init /var/log/syslog 2>/dev/null | tail -50",
            # Check if script even ran
            "ls -la /tmp/snapshot-setup-complete 2>/dev/null || echo 'Completion marker NOT found'",
            # Check systemd for failures
            "systemctl --failed 2>/dev/null | head -20",
        ]
        
        combined_cmd = " && echo '---SEPARATOR---' && ".join(log_commands)
        
        try:
            result = subprocess.run(
                [
                    "ssh",
                    "-i", str(self.DEPLOYER_KEY_PATH),
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "UserKnownHostsFile=/dev/null",
                    "-o", "ConnectTimeout=15",
                    "-o", "BatchMode=yes",
                    f"root@{droplet_ip}",
                    combined_cmd
                ],
                capture_output=True,
                text=True,
                timeout=45,
            )
            
            output = result.stdout.strip() if result.stdout else ""
            stderr = result.stderr.strip() if result.stderr else ""
            
            if output:
                return output
            elif stderr:
                return f"SSH stderr: {stderr}"
            else:
                return f"SSH returned empty (code {result.returncode})"
                
        except subprocess.TimeoutExpired:
            return "SSH timeout (45s) fetching logs"
        except Exception as e:
            return f"Error fetching logs: {type(e).__name__}: {e}"
    
    def _parse_error_from_logs(self, logs: str) -> Optional[str]:
        """
        Parse cloud-init logs to extract meaningful error message.
        
        Returns a human-readable error string or None if no error found.
        """
        if not logs:
            return None
        
        error_patterns = [
            # pip/package errors (order matters - more specific first)
            ("RECORD file not found", "Package conflict: cannot uninstall system package (try --ignore-installed)"),
            ("Cannot uninstall", "Package conflict: {line}"),
            ("externally-managed-environment", "Python env error: needs --break-system-packages or --ignore-installed"),
            ("No matching distribution", "Package not found: {line}"),
            ("pip3: command not found", "pip3 not installed - missing python3-pip"),
            ("pip: command not found", "pip not installed"),
            
            # apt errors
            ("Unable to locate package", "APT package not found: {line}"),
            ("E: Package", "APT error: {line}"),
            ("dpkg: error", "DPKG error: {line}"),
            ("apt-get: command not found", "apt-get not available"),
            
            # Docker errors
            ("Cannot connect to the Docker daemon", "Docker daemon not running"),
            ("docker: command not found", "Docker not installed"),
            ("Error response from daemon", "Docker error: {line}"),
            ("manifest unknown", "Docker image not found: {line}"),
            
            # General errors  
            ("command not found", "Command not found: {line}"),
            ("Permission denied", "Permission denied: {line}"),
            ("No such file or directory", "File not found: {line}"),
            ("Connection refused", "Connection refused: {line}"),
            ("curl: ", "Curl error: {line}"),
            
            # Cloud-init specific
            ("Failed to run module", "Cloud-init module failed: {line}"),
            ("WARNING]: Failed", "Cloud-init warning: {line}"),
        ]
        
        lines = logs.split('\n')
        errors_found = []
        
        for line in lines:
            line_stripped = line.strip()
            if not line_stripped:
                continue
                
            for pattern, message_template in error_patterns:
                if pattern.lower() in line_stripped.lower():
                    # Extract relevant part of the line
                    short_line = line_stripped[:100] + "..." if len(line_stripped) > 100 else line_stripped
                    error_msg = message_template.format(line=short_line)
                    if error_msg not in errors_found:
                        errors_found.append(error_msg)
                    break
        
        if errors_found:
            return "; ".join(errors_found[:3])  # Return top 3 errors
        
        return None
    
    def _power_off_droplet(self, droplet_id: int):
        """Power off droplet."""
        client = self._get_client()
        client.power_off_droplet(droplet_id)
    
    def _create_snapshot(self, droplet_id: int, name: str) -> Optional[str]:
        """Create snapshot from droplet."""
        client = self._get_client()
        try:
            result = client.create_snapshot_from_droplet(droplet_id, name)
            return str(result.get("id")) if result.get("id") else None
        except Exception:
            return None
    
    def _wait_for_snapshot(self, action_id: str, timeout: int = 600) -> bool:
        """Wait for snapshot action to complete."""
        client = self._get_client()
        start = time.time()
        while time.time() - start < timeout:
            action = client.get_action(int(action_id))
            status = action.get("status")
            if status == "completed":
                return True
            elif status == "errored":
                return False
            time.sleep(10)
        return False
    
    def _delete_droplet(self, droplet_id: int):
        """Delete droplet."""
        client = self._get_client()
        client.delete_droplet(droplet_id, force=True)
    
# Convenience function
def ensure_snapshot(
    do_token: str,
    config: SnapshotConfig,
    region: str = "lon1",
    size: str = "s-1vcpu-1gb",
    force_recreate: bool = False,
) -> SnapshotResult:
    """Convenience function to ensure snapshot exists."""
    service = SnapshotService(do_token)
    return service.ensure_snapshot(config, region, size, force_recreate)


# =============================================================================
# Async Snapshot Service
# =============================================================================

class AsyncSnapshotService:
    """
    Async version of SnapshotService for use in FastAPI.
    
    Uses AsyncDOClient from shared cloud module for all API calls.
    
    Provides async versions of common methods:
    - list_snapshots()
    - get_snapshot_by_name()
    - delete_snapshot()
    - transfer_snapshot_to_regions()
    - transfer_snapshot_to_all_regions()
    
    For streaming operations (ensure_snapshot_stream),
    use the sync SnapshotService with FastAPI's StreamingResponse.
    
    Usage:
        service = AsyncSnapshotService(do_token)
        snapshots = await service.list_snapshots()
        await service.delete_snapshot(snapshot_id)
    """
    
    def __init__(self, do_token: str):
        self.do_token = do_token
        self._client: Optional['AsyncDOClient'] = None
    
    def _get_client(self) -> 'AsyncDOClient':
        """Get or create async DO client."""
        if self._client is None:
            self._client = AsyncDOClient(self.do_token)
        return self._client
    
    async def close(self):
        """Close the client."""
        if self._client:
            await self._client.close()
            self._client = None
    
    @staticmethod
    def generate_api_key(do_token: str, user_id: str = "") -> str:
        """Generate deterministic API key (same as sync version)."""
        return SnapshotService.generate_api_key(do_token, user_id)
    
    async def list_snapshots(self) -> List[Dict[str, Any]]:
        """List all droplet snapshots."""
        client = self._get_client()
        return await client.list_snapshots()
    
    async def get_snapshot_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Find snapshot by name."""
        client = self._get_client()
        return await client.get_snapshot_by_name(name)
    
    async def delete_snapshot(self, snapshot_id: str) -> bool:
        """Delete a snapshot."""
        client = self._get_client()
        result = await client.delete_snapshot(snapshot_id)
        return result.success
    
    async def transfer_snapshot_to_regions(self, snapshot_id: str, regions: List[str]) -> bool:
        """Transfer a snapshot to multiple regions."""
        if not regions:
            return True
        
        client = self._get_client()
        try:
            await client.transfer_snapshot(snapshot_id, regions[0], wait=False)
            return True
        except Exception:
            return False
    
    async def transfer_snapshot_to_all_regions(self, snapshot_id: str) -> dict:
        """Transfer a snapshot to all available DO regions."""
        client = self._get_client()
        return await client.transfer_snapshot_to_all_regions(snapshot_id, wait=False)


# Async convenience function
async def ensure_snapshot_async(
    do_token: str,
    config: SnapshotConfig,
    region: str = "lon1",
    size: str = "s-1vcpu-1gb",
    force_recreate: bool = False,
) -> SnapshotResult:
    """
    Async convenience function to ensure snapshot exists.
    
    Note: For streaming progress, use SnapshotService.ensure_snapshot_stream()
    with FastAPI's StreamingResponse.
    """
    # For complex operations, we still use sync service in a thread
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: ensure_snapshot(do_token, config, region, size, force_recreate)
    )
