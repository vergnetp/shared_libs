"""
Command executor with HTTP agent support for specific operations and SSH for general commands.

SECURITY MODEL:
- Generic commands → SSH (safer, no RCE risk if API key compromised)
- Specific operations → Agent endpoints (file writes, mkdir, service control)
- localhost operations → Direct subprocess calls (unchanged)

ARCHITECTURE:
- localhost operations: Direct subprocess calls
- Remote generic commands: SSH (default, secure)
- Remote file/directory ops: HTTP agent (specific endpoints only)

USAGE:
    # Generic commands (uses SSH for security)
    CommandExecuter.run_cmd("systemctl status nginx", server_ip)
    
    # Specific operations (use agent with purpose-built endpoints)
    CommandExecuter.write_file(path, content, server_ip)  # Uses /files/write
    CommandExecuter.mkdir(path, server_ip)  # Uses /files/mkdir
"""

import subprocess
import shlex
import platform
from pathlib import Path
from typing import Union, List, Any, Optional

try:
    from .health_monitor import HealthMonitor
except ImportError:
    from health_monitor import HealthMonitor
try:
    from .logger import Logger
except ImportError:
    from logger import Logger


def log(msg):
    Logger.log(msg)


def parse_docker_error(error_text: str, cmd: Union[List[str], str]) -> str:
    """Parse Docker error messages for better user feedback"""
    MAX_CHARS = 500
    
    if "Cannot connect to the Docker daemon" in error_text:
        system = platform.system()
        if system == "Windows":
            return ("Docker error: Cannot connect to Docker daemon.\n"
                   "Please ensure Docker Desktop is running:\n"
                   "1. Start Docker Desktop application\n"
                   "2. Wait for it to fully start\n"
                   "3. Try your command again")
        else:
            return ("Docker error: Cannot connect to Docker daemon.\n"
                   "Please ensure Docker is installed and running:\n"
                   "- Check: sudo systemctl status docker\n"
                   "- Start: sudo systemctl start docker")
    
    if "no such file or directory" in error_text.lower() and "dockerfile" in error_text.lower():
        return f"Docker error: Dockerfile not found.\n{error_text[:MAX_CHARS]}"
    
    if "not found" in error_text.lower():
        if isinstance(cmd, list) and len(cmd) > 1:
            image = cmd[-1] if ":" in str(cmd[-1]) else "unknown"
            return f"Docker error: Image not found: {image}\n{error_text[:MAX_CHARS]}"
    
    # Fallback for other commands
    return f"Command failed: {error_text[:MAX_CHARS]}"


class CommandExecuter:
    """
    Execute commands locally or remotely.
    
    SECURITY MODEL:
    - Generic commands: SSH (secure, no arbitrary command execution via API)
    - Specific operations: Agent endpoints (file writes, mkdir, service control)
    - localhost: Direct subprocess calls
    
    The agent provides specific, safe endpoints rather than generic command execution
    to minimize security risk if the API key is ever compromised.
    """
    
    # Feature flag for agent-based operations (file writes, mkdir, etc.)
    USE_AGENT = True
    
    # Cache for agent availability per server
    _agent_available_cache = {}
    
    @staticmethod
    def is_agent_available(server_ip: str) -> bool:
        """
        Check if health agent is available on server.
        Caches result to avoid repeated checks.
        """
        if server_ip == 'localhost' or server_ip is None:
            return False
        
        # Check cache first
        if server_ip in CommandExecuter._agent_available_cache:
            return CommandExecuter._agent_available_cache[server_ip]
        
        # Try to ping agent
        try:
            response = HealthMonitor.agent_request(
                server_ip,
                "GET",
                "/ping",
                timeout=2
            )
            available = response.get('status') == 'alive'
            CommandExecuter._agent_available_cache[server_ip] = available
            return available
        except Exception:
            CommandExecuter._agent_available_cache[server_ip] = False
            return False

    @staticmethod
    def check_docker_available() -> bool:
        """Check if Docker is available and running"""
        try:
            result = subprocess.run(['docker', '--version'], capture_output=True, text=True, check=False)
            return result.returncode == 0
        except FileNotFoundError:
            return False

    @staticmethod
    def run_cmd(
        cmd: Union[List[str], str], 
        server_ip: str = 'localhost', 
        user: str = "root",
        use_ssh: bool = False  # Kept for API compatibility but always uses SSH for remote
    ) -> Any:
        """
        Run command locally or remotely.
        
        Args:
            cmd: Command to run (string or list)
            server_ip: Target server IP (localhost for local)
            user: SSH user (default: root)
            use_ssh: Kept for compatibility (remote commands always use SSH)
        
        Returns:
            Command output or subprocess.CompletedProcess
        """
        user = 'root'  # todo: clean that
        
        # Handle multiple commands case
        if isinstance(cmd, list) and len(cmd) > 0 and isinstance(cmd[0], str) and any(' ' in c for c in cmd):
            # This looks like a list of complete command strings
            results = []
            for single_cmd in cmd:
                if server_ip == 'localhost' or server_ip is None:
                    result = CommandExecuter._run_cmd_local(single_cmd)
                else:
                    result = CommandExecuter._run_cmd_remote(single_cmd, server_ip, user, use_ssh)
                results.append(result)
            return results
        
        # Single command case
        if server_ip == 'localhost' or server_ip is None:
            return CommandExecuter._run_cmd_local(cmd)
        else:
            return CommandExecuter._run_cmd_remote(cmd, server_ip, user, use_ssh)

    @staticmethod
    def _run_cmd_remote(
        cmd: Union[List[str], str], 
        server_ip: str, 
        user: str = "root",
        use_ssh: bool = False
    ) -> str:
        """
        Run command on remote server.
        
        For security, we use SSH for most operations.
        Agent is only used for specific operations via high-level methods.
        """
        # Normalize command to string
        if isinstance(cmd, list):
            cmd_list = CommandExecuter._normalize_command(cmd)
            cmd_str = " ".join(cmd_list)
        else:
            cmd_str = cmd
        
        # Use SSH for command execution
        # (Agent only used for specific operations like file writes, mkdir, etc.)
        return CommandExecuter._run_ssh_cmd(cmd_str, server_ip, user)

    @staticmethod
    def _normalize_command(cmd: Union[List[str], str]) -> List[str]:
        """Normalize command input to a proper argument list"""
        if isinstance(cmd, str):
            # Use shlex for proper shell-style parsing
            result = shlex.split(cmd)
        elif isinstance(cmd, list):
            # Flatten nested lists and convert all to strings
            result = []
            for item in cmd:
                if isinstance(item, str):
                    result.append(item)
                elif isinstance(item, list):
                    result.extend(str(x) for x in item)
                else:
                    result.append(str(item))
            return result
        else:
            raise ValueError(f"Invalid cmd type: {type(cmd)}. Expected str or list.")
        log(f"Executing command: {result}")
        return result

    @staticmethod
    def _run_cmd_local(cmd: Union[List[str], str]) -> subprocess.CompletedProcess:
        """Run local command with proper argument handling"""
        cmd_list = CommandExecuter._normalize_command(cmd)
        
        if not cmd_list:
            raise ValueError("Empty command")

        try:
            # Use UTF-8 encoding to handle Docker's Unicode output on Windows
            result = subprocess.run(
                cmd_list, 
                capture_output=True, 
                text=True, 
                encoding='utf-8', 
                errors='replace', 
                check=False
            )
            if result.returncode != 0:
                if result.returncode != 0:
                    if cmd_list[0] == "docker":
                        error_msg = parse_docker_error(result.stderr, cmd_list)
                    else:
                        error_msg = f"Command failed (exit code {result.returncode}): {result.stderr.strip()}"
                    raise Exception(error_msg)
            return result
        except FileNotFoundError as e:
            # Check if Docker is installed and accessible
            if cmd_list[0] == "docker":
                raise FileNotFoundError(
                    f"Docker command not found. Please ensure Docker Desktop is installed and running.\n"
                    f"Attempted command: {' '.join(cmd_list)}\n"
                    f"Original error: {e}"
                )
            else:
                raise FileNotFoundError(f"Command not found: {cmd_list[0]}\nFull command: {' '.join(cmd_list)}")

    @staticmethod
    def _run_ssh_cmd(cmd: Union[List[str], str], server_ip: str, user: str = "root") -> str:
        """Run command via SSH with cross-platform support (Docker on Windows)"""        
        user = 'root'  # todo: clean that
        
        # If it's already a string, use it as-is (it may contain shell operators)
        if isinstance(cmd, str):
            remote_cmd = cmd
        else:
            # Normalize the command first
            cmd_list = CommandExecuter._normalize_command(cmd)
            
            if not cmd_list:
                raise ValueError("Empty command")
            
            # Check if any shell operators are present - if so, treat as shell command
            cmd_str = " ".join(str(c) for c in cmd_list)
            shell_operators = [">", ">>", "|", "||", "&&", "2>", "2>&1", "<"]
            
            if any(op in cmd_str for op in shell_operators):
                # Contains shell operators - use as-is
                remote_cmd = cmd_str
            else:
                # Properly escape the remote command for SSH
                remote_cmd = " ".join(shlex.quote(arg) for arg in cmd_list)
        
        system = platform.system()
        ssh_key_path = Path.home() / ".ssh" / "deployer_id_rsa"
        
        if system == "Windows":
            # Use Docker with SSH client on Windows
            key_path_str = str(ssh_key_path).replace("\\", "/")
            if key_path_str[1] == ":":
                key_path_str = f"/{key_path_str[0].lower()}{key_path_str[2:]}"
            
            # For Windows Docker execution, we need to escape the remote command properly
            # The remote_cmd will be executed through: docker -> sh -c -> ssh -> remote shell
            # So we need proper quoting for the sh -c context
            escaped_remote_cmd = remote_cmd.replace("'", "'\\''")  # Escape single quotes for sh -c
            
            # FIX: Suppress Alpine package installation output by redirecting to /dev/null
            docker_ssh_cmd = [
                "docker", "run", "--rm",
                "-v", f"{key_path_str}:/root/.ssh/deployer_id_rsa",
                "alpine:latest",
                "sh", "-c",
                f"apk add --no-cache openssh-client >/dev/null 2>&1 && "
                f"chmod 600 /root/.ssh/deployer_id_rsa && "
                f"ssh -o StrictHostKeyChecking=no -i /root/.ssh/deployer_id_rsa {user}@{server_ip} '{escaped_remote_cmd}'"
            ]
            
            try:
                result = subprocess.run(docker_ssh_cmd, capture_output=True, text=True, 
                                    encoding='utf-8', errors='replace', check=False)
                if result.returncode != 0:
                    error_msg = f"SSH command failed on {server_ip} (exit code {result.returncode}): {result.stderr.strip()}"
                    raise Exception(error_msg)
                return result.stdout.strip()
            except FileNotFoundError:
                raise FileNotFoundError("Docker not found. "
                                      "Please ensure Docker Desktop is installed and running.")
        else:
            # Native SSH on Linux/macOS
            ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", 
                    "-i", str(ssh_key_path),
                    f"{user}@{server_ip}", remote_cmd]
            
            try:
                result = subprocess.run(ssh_cmd, capture_output=True, text=True, check=False)
                if result.returncode != 0:
                    if 'docker' in remote_cmd:
                        error_msg = parse_docker_error(result.stderr, ssh_cmd)
                    else:
                        error_msg = f"SSH command failed on {server_ip} (exit code {result.returncode}): {result.stderr.strip()}"
                    raise Exception(error_msg)
                return result.stdout.strip()
            except FileNotFoundError:
                raise FileNotFoundError("SSH client not found. Please install SSH.")

    @staticmethod
    def run_cmd_with_stdin(
        remote_cmd: str, 
        data: bytes, 
        server_ip: str, 
        user: str = "root",
        use_ssh: bool = False
    ) -> None:
        """
        Run a remote command and stream data to its stdin.
        
        MIGRATED: Uses agent for tar extraction and file writes if agent available.
        """
        user = 'root'  # todo: clean that
        
        # Check if agent is available (skip if use_ssh=True or agent not available)
        agent_available = (
            CommandExecuter.USE_AGENT and 
            not use_ssh and 
            CommandExecuter.is_agent_available(server_ip)
        )
        
        # Detect tar extraction operation
        if agent_available and 'tar -xzf -' in remote_cmd:
            try:
                # Extract path from command like "cd /path && tar -xzf -"
                import re
                cd_match = re.search(r'cd\s+([^\s&]+)', remote_cmd)
                if cd_match:
                    extract_path = cd_match.group(1)
                    
                    # Use agent's tar upload endpoint
                    import base64
                    tar_base64 = base64.b64encode(data).decode('utf-8')
                    
                    response = HealthMonitor.agent_request(
                        server_ip,
                        "POST",
                        "/files/upload",
                        json_data={
                            'tar_data': tar_base64,
                            'extract_path': extract_path,
                            'set_permissions': False  # Will be set separately if needed
                        },
                        timeout=120  # Longer timeout for large uploads
                    )
                    
                    if response.get('status') == 'success':
                        log(f"✓ Uploaded via agent: {response.get('files_extracted')} files")
                        return
                    else:
                        raise Exception(f"Tar upload failed: {response.get('error', 'unknown')}")
                        
            except Exception as e:
                log(f"Agent call failed for tar upload, falling back to SSH: {e}")
                # Fall through to SSH
        
        # Try to detect if this is a file write operation
        if agent_available and 'cat >' in remote_cmd:
            try:
                # Extract file path from command like "cat > /path/to/file"
                parts = remote_cmd.split('cat >')
                if len(parts) == 2:
                    file_path = parts[1].split('&&')[0].strip()
                    content = data.decode('utf-8')
                    
                    # Use agent's file write endpoint
                    response = HealthMonitor.agent_request(
                        server_ip,
                        "POST",
                        "/files/write",
                        json_data={
                            'path': file_path,
                            'content': content,
                            'permissions': '644'  # Default, can be overridden
                        },
                        timeout=30
                    )
                    
                    if response.get('status') == 'success':
                        return
                    else:
                        raise Exception(f"File write failed: {response.get('error', 'unknown')}")
                        
            except Exception as e:
                log(f"Agent call failed for file write, falling back to SSH: {e}")
                # Fall through to SSH
        
        # Use SSH (fallback or agent not available)
        CommandExecuter._run_ssh_cmd_with_stdin(remote_cmd, data, server_ip, user)

    @staticmethod
    def _run_ssh_cmd_with_stdin(remote_cmd: str, data: bytes, server_ip: str, user: str = "root") -> None:
        """Run a remote command via SSH and stream data to its stdin"""
        ssh_key_path = Path.home() / ".ssh" / "deployer_id_rsa"
        system = platform.system()

        if system == "Windows":
            # Use Dockerized SSH same as _run_ssh_cmd
            key_path_str = str(ssh_key_path).replace("\\", "/")
            if key_path_str[1] == ":":
                key_path_str = f"/{key_path_str[0].lower()}{key_path_str[2:]}"
            
            # FIX: Suppress Alpine package installation output
            ssh_wrapper = [
                "docker", "run", "--rm",
                "-i",  # keep stdin open
                "-v", f"{key_path_str}:/root/.ssh/deployer_id_rsa",
                "alpine:latest",
                "sh", "-c",
                f"apk add --no-cache openssh-client >/dev/null 2>&1 && "
                f"chmod 600 /root/.ssh/deployer_id_rsa && "
                f"ssh -o StrictHostKeyChecking=no -i /root/.ssh/deployer_id_rsa {user}@{server_ip} {shlex.quote(remote_cmd)}"
            ]
        else:
            ssh_wrapper = [
                "ssh", "-o", "StrictHostKeyChecking=no",
                "-i", str(ssh_key_path),
                f"{user}@{server_ip}", remote_cmd
            ]

        result = subprocess.run(ssh_wrapper, input=data, capture_output=True)
        if result.returncode != 0:
            raise Exception(f"SSH stdin transfer failed: {result.stderr.decode('utf-8', 'replace')}")

    # =========================================================================
    # HIGH-LEVEL FILE OPERATIONS (use agent automatically)
    # =========================================================================

    @staticmethod
    def write_file(
        path: str, 
        content: str, 
        server_ip: str = 'localhost',
        permissions: str = '644',
        use_ssh: bool = False
    ) -> bool:
        """
        Write file to server.
        
        MIGRATED: Uses HTTP agent for remote operations if available.
        
        Args:
            path: File path
            content: File content
            server_ip: Target server
            permissions: File permissions (octal string like '644')
            use_ssh: Force SSH instead of agent
        
        Returns:
            True on success
        """
        # Localhost - direct write
        if server_ip == 'localhost' or server_ip is None:
            file_path = Path(path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content)
            file_path.chmod(int(permissions, 8))
            return True
        
        # Remote - try agent if available
        agent_available = (
            CommandExecuter.USE_AGENT and 
            not use_ssh and 
            CommandExecuter.is_agent_available(server_ip)
        )
        
        if agent_available:
            try:
                response = HealthMonitor.agent_request(
                    server_ip,
                    "POST",
                    "/files/write",
                    json_data={
                        'path': path,
                        'content': content,
                        'permissions': permissions
                    },
                    timeout=30
                )
                
                if response.get('status') == 'success':
                    return True
                else:
                    raise Exception(f"File write failed: {response.get('error', 'unknown')}")
                    
            except Exception as e:
                log(f"Agent call failed for file write, falling back to SSH: {e}")
                # Fall through to SSH
        
        # SSH fallback
        CommandExecuter.run_cmd_with_stdin(
            f"mkdir -p $(dirname {path}) && cat > {path} && chmod {permissions} {path}",
            content.encode('utf-8'),
            server_ip
        )
        return True

    @staticmethod
    def mkdir(
        path: str, 
        server_ip: str = 'localhost',
        mode: str = '755',
        use_ssh: bool = False
    ) -> bool:
        """
        Create directory on server.
        
        MIGRATED: Uses HTTP agent for remote operations if available.
        
        Args:
            path: Directory path
            server_ip: Target server
            mode: Directory permissions (octal string like '755')
            use_ssh: Force SSH instead of agent
        
        Returns:
            True on success
        """
        # Localhost - direct creation
        if server_ip == 'localhost' or server_ip is None:
            dir_path = Path(path)
            dir_path.mkdir(parents=True, exist_ok=True)
            dir_path.chmod(int(mode, 8))
            return True
        
        # Remote - try agent if available
        agent_available = (
            CommandExecuter.USE_AGENT and 
            not use_ssh and 
            CommandExecuter.is_agent_available(server_ip)
        )
        
        if agent_available:
            try:
                response = HealthMonitor.agent_request(
                    server_ip,
                    "POST",
                    "/files/mkdir",
                    json_data={
                        'paths': [path],
                        'mode': mode
                    },
                    timeout=30
                )
                
                if response.get('status') == 'success':
                    return True
                else:
                    raise Exception(f"mkdir failed: {response.get('error', 'unknown')}")
                    
            except Exception as e:
                log(f"Agent call failed for mkdir, falling back to SSH: {e}")
                # Fall through to SSH
        
        # SSH fallback
        CommandExecuter.run_cmd(f'mkdir -p {path} && chmod {mode} {path}', server_ip)
        return True