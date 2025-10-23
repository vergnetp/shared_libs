import subprocess
from pathlib import Path
import shlex
from typing import Union, List, Any
import platform

try:
    from .logger import Logger
except ImportError:
    from logger import Logger


def log(msg):
    Logger.log(msg)


def parse_docker_error(error_text: str, cmd: List[str]) -> str:
    """
    Parse Docker error and provide user-friendly messages without overwriting actual errors.

    Logic:
    - Detect Docker not installed
    - Detect Docker Desktop connectivity issues (Windows/WSL2)
    - Detect Docker daemon not running
    - Detect Docker permission issues (Linux)
    - Always show the real error for other Docker commands
    """

    if not error_text:
        error_text = ""

    error_lower = error_text.lower()

    MAX_CHARS = 2000
    
    # Docker not installed
    if "no such file or directory" in error_lower or "command not found" in error_lower:
        return (
            "Docker is not installed.\n\n"
            "Please install Docker:\n"
            "- Linux: https://docs.docker.com/engine/install/\n"
            "- macOS/Windows: https://docs.docker.com/desktop/\n"
            f"Error: {error_text[:MAX_CHARS]}"
        )

    # Docker Desktop connectivity issues (Windows)
    if "dockerdesktoplinuxengine" in error_lower and "pipe" in error_lower:
        return (
            f"Docker command failed.\nCommand: {' '.join(cmd)}\nError: {error_text[:MAX_CHARS]}\n\n"
            "Docker Desktop connectivity issue (Windows).\n"
            "Try:\n"
            "1. Start Docker Desktop\n"
            "2. Wait for whale icon to show 'running'\n"
            "3. If already running, restart Docker Desktop\n"
            "4. Run `wsl --update` in PowerShell (admin)"
        )

    # Docker daemon not running
    if ("cannot connect to the docker daemon" in error_lower or 
        "docker daemon is not running" in error_lower or
        "connection refused" in error_lower):
        return (
            f"Docker command failed.\nCommand: {' '.join(cmd)}\nError: {error_text[:MAX_CHARS]}\n\n"
            "Docker daemon is not running.\n"
            "Start Docker:\n"
            "- Linux: sudo systemctl start docker\n"
            "- macOS: Start Docker Desktop\n"
            "- Windows: Start Docker Desktop"
        )

    # Permission issues (Linux)
    if "permission denied" in error_lower and "docker.sock" in error_lower:
        return (
            f"Docker command failed.\nCommand: {' '.join(cmd)}\nError: {error_text[:MAX_CHARS]}\n\n"
            "Docker permission issue.\nFix with:\n"
            "- sudo usermod -aG docker $USER\n"
            "- Log out/in or run: newgrp docker\n"
            "- Or use sudo (not recommended)"
        )

    # Generic Docker command errors
    if cmd and cmd[0] == "docker":
        return f"Docker command failed.\nCommand: {' '.join(cmd)}\nError: {error_text[:MAX_CHARS]}"

    # Fallback for other commands
    return f"Command failed: {error_text[:MAX_CHARS]}"


class CommandExecuter:
    """Execute commands locally or via SSH with robust argument handling"""

    @staticmethod
    def check_docker_available() -> bool:
        """Check if Docker is available and running"""
        try:
            result = subprocess.run(['docker', '--version'], capture_output=True, text=True, check=False)
            return result.returncode == 0
        except FileNotFoundError:
            return False

    @staticmethod
    def run_cmd(cmd: Union[List[str], str], server_ip: str = 'localhost', user: str = "root") -> Any:
        """Run command(s) locally or via SSH depending on server_ip"""
        # Handle multiple commands case
        if isinstance(cmd, list) and len(cmd) > 0 and isinstance(cmd[0], str) and any(' ' in c for c in cmd):
            # This looks like a list of complete command strings
            results = []
            for single_cmd in cmd:
                if server_ip == 'localhost' or server_ip is None:
                    result = CommandExecuter._run_cmd_local(single_cmd)
                else:
                    result = CommandExecuter._run_ssh_cmd(single_cmd, server_ip, user)
                results.append(result)
            return results
        
        # Single command case
        if server_ip == 'localhost' or server_ip is None:
            return CommandExecuter._run_cmd_local(cmd)
        else:
            return CommandExecuter._run_ssh_cmd(cmd, server_ip, user)

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
            
            docker_ssh_cmd = [
                "docker", "run", "--rm",
                "-v", f"{key_path_str}:/root/.ssh/deployer_id_rsa",
                "alpine:latest",
                "sh", "-c",
                f"apk add --no-cache openssh-client && "
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
                raise FileNotFoundError("Docker not found. Please ensure Docker Desktop is installed and running.")
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
    def run_cmd_with_stdin(remote_cmd: str, data: bytes, server_ip: str, user: str = "root") -> None:
        """Run a remote command and stream data to its stdin (avoids huge base64 echoes)."""

        ssh_key_path = Path.home() / ".ssh" / "deployer_id_rsa"
        system = platform.system()

        if system == "Windows":
            # Use Dockerized SSH same as _run_ssh_cmd
            key_path_str = str(ssh_key_path).replace("\\", "/")
            if key_path_str[1] == ":":
                key_path_str = f"/{key_path_str[0].lower()}{key_path_str[2:]}"
            ssh_wrapper = [
                "docker", "run", "--rm",
                "-i",  # keep stdin open
                "-v", f"{key_path_str}:/root/.ssh/deployer_id_rsa",
                "alpine:latest",
                "sh", "-c",
                f"apk add --no-cache openssh-client && "
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
