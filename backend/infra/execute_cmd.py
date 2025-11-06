"""
Command executor with intelligent pattern-based routing to HTTP agent.

ARCHITECTURE V2 - INTERCEPTOR PATTERN:
- localhost operations: Direct subprocess calls
- Remote operations: Pattern matching → Agent endpoints (with SSH fallback)
- Unmatched patterns: SSH fallback

SECURITY MODEL:
- No generic command execution via agent (only specific endpoints)
- All patterns mapped to purpose-built agent endpoints
- SSH fallback for unmapped commands and when agent unavailable

The interceptor routes common command patterns (docker ps, systemctl, etc.) 
to specific agent endpoints, eliminating SSH for 90%+ of operations while
maintaining security through explicit endpoint mapping.
"""

import subprocess
import shlex
import platform
import re
from pathlib import Path
from typing import Union, List, Any, Optional, Dict, Tuple

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
    Execute commands locally or remotely with intelligent pattern-based routing.
    """
    
    # Feature flag for agent-based operations
    USE_AGENT = True
    
    # Cache for agent availability per server
    _agent_available_cache = {}
    
    # =========================================================================
    # COMMAND PATTERN ROUTES - Maps command patterns to agent endpoints
    # =========================================================================
    
    @staticmethod
    def _get_command_routes() -> List[Dict]:
        """
        Define command patterns and their agent endpoint mappings.
        
        Each route contains:
        - pattern: Regex pattern to match command
        - endpoint: Agent endpoint (supports {match1}, {match2} placeholders)
        - method: HTTP method (GET, POST, DELETE)
        - parser: Optional function to extract additional params from command
        - formatter: Function to convert agent response to SSH-like output
        """
        return [
            # ============= DOCKER PS COMMANDS =============
            {
                'pattern': r'^docker\s+ps\s+-a\s+--filter\s+["\']?name=([^"\'\s]+)["\']?\s+--format\s+["\'](.+?)["\']',
                'endpoint': '/docker/ps',
                'method': 'GET',
                'parser': lambda cmd, match: {
                    'filter_name': match[0],
                    'format': match[1]
                },
                'formatter': lambda resp: resp.get('output', '')
            },
            {
                'pattern': r'^docker\s+ps\s+--format\s+["\'](.+?)["\']',
                'endpoint': '/docker/ps',
                'method': 'GET',
                'parser': lambda cmd, match: {'format': match[0], 'all': False},
                'formatter': lambda resp: resp.get('output', '')
            },
            {
                'pattern': r'^docker\s+ps\s+-a',
                'endpoint': '/docker/ps',
                'method': 'GET',
                'parser': lambda cmd, match: {'all': True},
                'formatter': lambda resp: resp.get('output', '')
            },
            {
                'pattern': r'^docker\s+ps',
                'endpoint': '/docker/ps',
                'method': 'GET',
                'parser': lambda cmd, match: {'all': False},
                'formatter': lambda resp: resp.get('output', '')
            },
            
            # ============= DOCKER LOGS =============
            {
                'pattern': r'^docker\s+logs\s+(?:--tail\s+(\d+)\s+)?([^\s]+)',
                'endpoint': '/containers/{match2}/logs',
                'method': 'GET',
                'parser': lambda cmd, match: {'lines': int(match[0]) if match[0] else 100},
                'formatter': lambda resp: resp.get('logs', '')
            },
            {
                'pattern': r'^docker\s+logs\s+(?:--timestamps\s+)?(?:--tail\s+(\d+)\s+)?([^\s]+)',
                'endpoint': '/containers/{match2}/logs',
                'method': 'GET',
                'parser': lambda cmd, match: {
                    'lines': int(match[0]) if match[0] else 100,
                    'timestamps': '--timestamps' in cmd
                },
                'formatter': lambda resp: resp.get('logs', '')
            },
            
            # ============= DOCKER INSPECT =============
            {
                'pattern': r'^docker\s+inspect\s+([^\s]+)\s+--format\s+["\'](.+?)["\']',
                'endpoint': '/containers/{match1}/inspect',
                'method': 'GET',
                'parser': lambda cmd, match: {'format': match[1]},
                'formatter': lambda resp: resp.get('output', '')
            },
            {
                'pattern': r'^docker\s+inspect\s+([^\s]+)',
                'endpoint': '/containers/{match1}/inspect',
                'method': 'GET',
                'formatter': lambda resp: resp.get('output', '')
            },
            
            # ============= DOCKER PORT =============
            {
                'pattern': r'^docker\s+port\s+([^\s]+)',
                'endpoint': '/containers/{match1}/port',
                'method': 'GET',
                'formatter': lambda resp: '\n'.join(
                    f"{cp} -> 0.0.0.0:{hp}" 
                    for cp, hp in resp.get('ports', {}).items()
                )
            },
            
            # ============= DOCKER STOP/START/RM =============
            {
                'pattern': r'^docker\s+stop\s+([^\s]+)',
                'endpoint': '/containers/{match1}/stop',
                'method': 'POST',
                'formatter': lambda resp: resp.get('container', '')
            },
            {
                'pattern': r'^docker\s+start\s+([^\s]+)',
                'endpoint': '/containers/{match1}/start',
                'method': 'POST',
                'formatter': lambda resp: resp.get('container', '')
            },
            {
                'pattern': r'^docker\s+rm\s+([^\s]+)',
                'endpoint': '/containers/{match1}/remove',
                'method': 'DELETE',
                'formatter': lambda resp: resp.get('container', '')
            },
            
            # ============= SYSTEMCTL COMMANDS =============
            {
                'pattern': r'^systemctl\s+(?:is-active|status)\s+([^\s]+)',
                'endpoint': '/system/service/{match1}/status',
                'method': 'GET',
                'formatter': lambda resp: resp.get('status', 'unknown')
            },
            {
                'pattern': r'^systemctl\s+start\s+([^\s]+)',
                'endpoint': '/system/service/{match1}/start',
                'method': 'POST',
                'formatter': lambda resp: ''  # systemctl start has no output on success
            },
            {
                'pattern': r'^systemctl\s+stop\s+([^\s]+)',
                'endpoint': '/system/service/{match1}/stop',
                'method': 'POST',
                'formatter': lambda resp: ''
            },
            {
                'pattern': r'^systemctl\s+enable\s+([^\s]+)',
                'endpoint': '/system/service/{match1}/enable',
                'method': 'POST',
                'formatter': lambda resp: ''
            },
            {
                'pattern': r'^systemctl\s+restart\s+([^\s]+)',
                'endpoint': '/system/service/{match1}/restart',
                'method': 'POST',
                'formatter': lambda resp: ''
            },
            {
                'pattern': r'^systemctl\s+daemon-reload',
                'endpoint': '/system/service/daemon-reload',
                'method': 'POST',
                'formatter': lambda resp: ''
            },
            
            # ============= CRON COMMANDS =============
            {
                'pattern': r'^crontab\s+-l',
                'endpoint': '/system/crontab',
                'method': 'GET',
                'formatter': lambda resp: resp.get('crontab', '')
            },
        ]
    
    # =========================================================================
    # AGENT ROUTING LOGIC
    # =========================================================================
    
    @staticmethod
    def _try_route_to_agent(cmd_str: str, server_ip: str) -> Optional[str]:
        """
        Try to route command to agent endpoint using pattern matching.
        Returns SSH-like output string on success, None if no route found.
        """
        if not CommandExecuter.is_agent_available(server_ip):
            return None
        
        # Clean up command string
        cmd_clean = cmd_str.strip()
        
        # Try to match against all route patterns
        for route in CommandExecuter._get_command_routes():
            match = re.match(route['pattern'], cmd_clean)
            if match:
                try:
                    # Build endpoint with match groups
                    endpoint = route['endpoint']
                    for i, group in enumerate(match.groups(), 1):
                        if group:  # Only replace if group matched
                            endpoint = endpoint.replace(f'{{match{i}}}', group)
                    
                    # Parse additional parameters if parser provided
                    params = {}
                    if 'parser' in route:
                        params = route['parser'](cmd_clean, match.groups())
                    
                    # Make agent request
                    if route['method'] == 'GET':
                        response = HealthMonitor.agent_request(
                            server_ip, 'GET', endpoint,
                            params=params, timeout=30
                        )
                    elif route['method'] in ['POST', 'DELETE']:
                        response = HealthMonitor.agent_request(
                            server_ip, route['method'], endpoint,
                            json_data=params, timeout=30
                        )
                    else:
                        raise ValueError(f"Unsupported method: {route['method']}")
                    
                    # Check for error response
                    if response.get('error'):
                        raise Exception(response['error'])
                    
                    # Format response using route's formatter
                    if 'formatter' in route:
                        return route['formatter'](response)
                    else:
                        # Default: return output field
                        return response.get('output', '')
                    
                except Exception as e:
                    log(f"Agent route failed for '{cmd_clean}', falling back to SSH: {e}")
                    return None
        
        # No matching route found
        return None
    
    # =========================================================================
    # AGENT AVAILABILITY CHECK
    # =========================================================================
    
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

    # =========================================================================
    # MAIN COMMAND EXECUTION ENTRY POINTS
    # =========================================================================
    
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
        use_ssh: bool = False
    ) -> Any:
        """
        Run command locally or remotely with intelligent routing.
        
        For remote commands:
        1. Try to match pattern and route to agent endpoint
        2. Fall back to SSH if no pattern match or agent unavailable
        
        Args:
            cmd: Command to run (string or list)
            server_ip: Target server IP (localhost for local)
            user: SSH user (default: root)
            use_ssh: Force SSH instead of agent routing
        
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
        Run command on remote server with intelligent routing.
        
        1. Try pattern matching → agent endpoint
        2. Fall back to SSH if no match or forced
        """
        # Normalize command to string
        if isinstance(cmd, list):
            cmd_list = CommandExecuter._normalize_command(cmd)
            cmd_str = " ".join(cmd_list)
        else:
            cmd_str = cmd
        
        # Try agent routing first (unless forced SSH)
        if not use_ssh and CommandExecuter.USE_AGENT:
            agent_result = CommandExecuter._try_route_to_agent(cmd_str, server_ip)
            if agent_result is not None:
                return agent_result
        
        # Fall back to SSH
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

    # =========================================================================
    # STDIN OPERATIONS
    # =========================================================================
    
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
        # Localhost - direct stdin
        if server_ip == 'localhost' or server_ip is None:
            result = subprocess.run(remote_cmd, shell=True, input=data, capture_output=True)
            if result.returncode != 0:
                raise Exception(f"Command failed: {result.stderr.decode('utf-8', 'replace')}")
            return
        
        # Remote - check if agent available and can handle this operation
        agent_available = (
            CommandExecuter.USE_AGENT and 
            not use_ssh and 
            CommandExecuter.is_agent_available(server_ip)
        )
        
        # Try to detect if this is a tar extraction operation
        if agent_available and 'tar' in remote_cmd and '-xzf' in remote_cmd:
            try:
                # Extract target directory from command
                # Format: "cd /path && tar -xzf -"
                if 'cd ' in remote_cmd:
                    target_dir = remote_cmd.split('cd ')[1].split('&&')[0].strip()
                    
                    # Use agent's tar upload endpoint
                    response = HealthMonitor.agent_request(
                        server_ip,
                        "POST",
                        "/files/upload/tar",
                        json_data={
                            'target_dir': target_dir,
                            'tar_data': base64.b64encode(data).decode('utf-8')
                        },
                        timeout=300  # 5 minutes for large uploads
                    )
                    
                    if response.get('status') == 'success':
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
        # Localhost - direct mkdir
        if server_ip == 'localhost' or server_ip is None:
            Path(path).mkdir(parents=True, exist_ok=True, mode=int(mode, 8))
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
                        'path': path,
                        'mode': mode
                    },
                    timeout=30
                )
                
                if response.get('status') == 'success':
                    return True
                else:
                    raise Exception(f"Mkdir failed: {response.get('error', 'unknown')}")
                    
            except Exception as e:
                log(f"Agent call failed for mkdir, falling back to SSH: {e}")
                # Fall through to SSH
        
        # SSH fallback
        CommandExecuter.run_cmd(f"mkdir -p {path} && chmod {mode} {path}", server_ip)
        return True