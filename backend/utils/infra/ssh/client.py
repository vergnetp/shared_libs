"""
SSH Client - Remote command execution.

Clean interface for executing commands on remote servers.
"""

from __future__ import annotations
import subprocess
import shlex
from pathlib import Path
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, Any, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

if TYPE_CHECKING:
    from ..context import DeploymentContext

from ..core.result import Result


# Default SSH key location (matches DOClient)
DEPLOYER_KEY_PATH = Path.home() / ".ssh" / "id_ed25519"


@dataclass
class SSHConfig:
    """SSH connection configuration."""
    user: str = "root"
    port: int = 22
    key_file: Optional[str] = field(default_factory=lambda: str(DEPLOYER_KEY_PATH) if DEPLOYER_KEY_PATH.exists() else None)
    connect_timeout: int = 10
    strict_host_checking: bool = False


class SSHClient:
    """
    SSH client for remote command execution.
    
    Usage:
        ssh = SSHClient(ctx)
        
        # Execute single command
        code, output = ssh.exec("1.2.3.4", "docker ps")
        
        # Execute on multiple servers in parallel
        results = ssh.exec_parallel(
            ["1.2.3.4", "1.2.3.5"],
            "docker pull nginx:latest"
        )
        
        # Upload file
        ssh.upload("1.2.3.4", "local.txt", "/remote/path.txt")
    """
    
    def __init__(
        self, 
        ctx: 'DeploymentContext',
        config: Optional[SSHConfig] = None,
    ):
        """
        Initialize SSH client.
        
        Args:
            ctx: Deployment context
            config: SSH configuration
        """
        self.ctx = ctx
        self.config = config or SSHConfig()
    
    # =========================================================================
    # Command Execution
    # =========================================================================
    
    def exec(
        self,
        server: str,
        command: str,
        timeout: int = 300,
        capture: bool = True,
    ) -> Tuple[int, str, str]:
        """
        Execute command on remote server.
        
        Args:
            server: Server IP or hostname
            command: Command to execute
            timeout: Timeout in seconds
            capture: Capture output
            
        Returns:
            Tuple of (return_code, stdout, stderr)
        """
        ssh_args = self._build_ssh_args(server)
        full_cmd = f"ssh {' '.join(ssh_args)} {shlex.quote(command)}"
        
        try:
            result = subprocess.run(
                full_cmd,
                shell=True,
                capture_output=capture,
                text=True,
                timeout=timeout,
            )
            return result.returncode, result.stdout, result.stderr
            
        except subprocess.TimeoutExpired:
            return -1, "", f"Command timed out after {timeout}s"
        except Exception as e:
            return -1, "", str(e)
    
    def exec_result(
        self,
        server: str,
        command: str,
        timeout: int = 300,
    ) -> Result:
        """
        Execute command and return Result.
        
        Args:
            server: Server IP
            command: Command to execute
            timeout: Timeout in seconds
            
        Returns:
            Result object
        """
        code, stdout, stderr = self.exec(server, command, timeout)
        
        if code == 0:
            return Result.ok(stdout.strip(), server=server)
        else:
            return Result.fail(stderr.strip() or f"Exit code {code}", server=server)
    
    def exec_parallel(
        self,
        servers: List[str],
        command: str,
        max_workers: int = 10,
        timeout: int = 300,
    ) -> Dict[str, Tuple[int, str, str]]:
        """
        Execute command on multiple servers in parallel.
        
        Args:
            servers: List of server IPs
            command: Command to execute
            max_workers: Max parallel connections
            timeout: Timeout per server
            
        Returns:
            Dict mapping server IP to (code, stdout, stderr)
        """
        results = {}
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.exec, server, command, timeout): server
                for server in servers
            }
            
            for future in as_completed(futures):
                server = futures[future]
                try:
                    results[server] = future.result()
                except Exception as e:
                    results[server] = (-1, "", str(e))
        
        return results
    
    # =========================================================================
    # File Transfer
    # =========================================================================
    
    def upload(
        self,
        server: str,
        local_path: str,
        remote_path: str,
        recursive: bool = False,
    ) -> Result:
        """
        Upload file or directory to remote server.
        
        Args:
            server: Server IP
            local_path: Local file/directory path
            remote_path: Remote destination path
            recursive: Recursive copy (for directories)
            
        Returns:
            Result object
        """
        scp_args = self._build_scp_args(server, remote_path)
        
        if recursive:
            scp_args.insert(0, "-r")
        
        cmd = f"scp {' '.join(scp_args[:-1])} {shlex.quote(local_path)} {scp_args[-1]}"
        
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
            
            if result.returncode == 0:
                return Result.ok(f"Uploaded {local_path} to {server}:{remote_path}")
            else:
                return Result.fail(result.stderr.strip())
                
        except Exception as e:
            return Result.fail(str(e))
    
    def download(
        self,
        server: str,
        remote_path: str,
        local_path: str,
        recursive: bool = False,
    ) -> Result:
        """
        Download file or directory from remote server.
        
        Args:
            server: Server IP
            remote_path: Remote file/directory path
            local_path: Local destination path
            recursive: Recursive copy (for directories)
            
        Returns:
            Result object
        """
        scp_args = self._build_scp_args(server, remote_path)
        
        args = []
        if recursive:
            args.append("-r")
        args.extend(scp_args[:-1])  # SSH options
        args.append(f"{self.config.user}@{server}:{remote_path}")
        args.append(local_path)
        
        cmd = f"scp {' '.join(args)}"
        
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
            
            if result.returncode == 0:
                return Result.ok(f"Downloaded {server}:{remote_path} to {local_path}")
            else:
                return Result.fail(result.stderr.strip())
                
        except Exception as e:
            return Result.fail(str(e))
    
    def upload_content(
        self,
        server: str,
        content: str,
        remote_path: str,
    ) -> Result:
        """
        Upload string content directly to remote file.
        
        Args:
            server: Server IP
            content: File content
            remote_path: Remote file path
            
        Returns:
            Result object
        """
        # Use echo with base64 to handle special characters
        import base64
        encoded = base64.b64encode(content.encode()).decode()
        
        cmd = f"echo {shlex.quote(encoded)} | base64 -d > {shlex.quote(remote_path)}"
        code, stdout, stderr = self.exec(server, cmd)
        
        if code == 0:
            return Result.ok(f"Created {remote_path} on {server}")
        else:
            return Result.fail(stderr.strip())
    
    # =========================================================================
    # Server Operations
    # =========================================================================
    
    def ping(self, server: str) -> bool:
        """Check if server is reachable via SSH."""
        code, _, _ = self.exec(server, "echo ok", timeout=10)
        return code == 0
    
    def ensure_directory(self, server: str, path: str) -> Result:
        """Ensure directory exists on remote server."""
        return self.exec_result(server, f"mkdir -p {shlex.quote(path)}")
    
    def file_exists(self, server: str, path: str) -> bool:
        """Check if file exists on remote server."""
        code, _, _ = self.exec(server, f"test -f {shlex.quote(path)}")
        return code == 0
    
    def read_file(self, server: str, path: str) -> Optional[str]:
        """Read file content from remote server."""
        code, stdout, _ = self.exec(server, f"cat {shlex.quote(path)}")
        return stdout if code == 0 else None
    
    # =========================================================================
    # Helpers
    # =========================================================================
    
    def _build_ssh_args(self, server: str) -> List[str]:
        """Build SSH command arguments."""
        args = []
        
        if not self.config.strict_host_checking:
            args.extend(["-o", "StrictHostKeyChecking=no"])
            args.extend(["-o", "UserKnownHostsFile=/dev/null"])
        
        args.extend(["-o", f"ConnectTimeout={self.config.connect_timeout}"])
        
        if self.config.key_file:
            args.extend(["-i", self.config.key_file])
        
        if self.config.port != 22:
            args.extend(["-p", str(self.config.port)])
        
        args.append(f"{self.config.user}@{server}")
        
        return args
    
    def _build_scp_args(self, server: str, remote_path: str) -> List[str]:
        """Build SCP command arguments."""
        args = []
        
        if not self.config.strict_host_checking:
            args.extend(["-o", "StrictHostKeyChecking=no"])
            args.extend(["-o", "UserKnownHostsFile=/dev/null"])
        
        if self.config.key_file:
            args.extend(["-i", self.config.key_file])
        
        if self.config.port != 22:
            args.extend(["-P", str(self.config.port)])
        
        args.append(f"{self.config.user}@{server}:{remote_path}")
        
        return args
