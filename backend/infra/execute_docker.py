import subprocess
import shlex
import time
import os
import platform
from typing import Union, List, Optional, Dict, Any
from execute_cmd import CommandExecuter
from logger import Logger


def log(msg):
    Logger.log(msg)


class DockerExecuter:
    """Docker-specific command executor with specialized methods"""

    @staticmethod
    def check_docker() -> bool:
        """Check if Docker is available and running"""
        return CommandExecuter.check_docker_available()

    @staticmethod
    def build_image(dockerfile_path: str, tag: str, context_dir: str = ".", 
                   build_args: Optional[Dict[str, str]] = None, 
                   no_cache: bool = False,
                   progress: str = "plain",
                   extra_args: Optional[List[str]] = None) -> Any:
        """Build Docker image locally with comprehensive options"""
        cmd = ["docker", "build"]
        
        if no_cache:
            cmd.append("--no-cache")
        
        if progress:
            cmd.extend(["--progress", progress])
        
        cmd.extend(["-f", dockerfile_path, "-t", tag])
        
        if build_args:
            for key, value in build_args.items():
                cmd.extend(["--build-arg", f"{key}={value}"])
        
        if extra_args:
            cmd.extend(extra_args)
        
        cmd.append(context_dir)
        
        return CommandExecuter.run_cmd(cmd)

    @staticmethod
    def run_container(image: str, name: Optional[str] = None, 
                     ports: Optional[Dict[str, str]] = None,
                     volumes: Optional[List[str]] = None,
                     environment: Optional[Dict[str, str]] = None,
                     network: Optional[str] = None,
                     detach: bool = True,
                     restart_policy: str = "unless-stopped",
                     server_ip: str = 'localhost', user: str = "root",
                     extra_args: Optional[List[str]] = None) -> Any:
        """Run Docker container with comprehensive parameters"""
        cmd = ["docker", "run"]
        
        if detach:
            cmd.append("-d")
        
        if name:
            cmd.extend(["--name", name])
        
        if network:
            cmd.extend(["--network", network])
        
        if restart_policy:
            cmd.extend(["--restart", restart_policy])
        
        if ports:
            for host_port, container_port in ports.items():
                cmd.extend(["-p", f"{host_port}:{container_port}"])
        
        if volumes:
            for volume in volumes:
                # Validate volume syntax - must contain : or be a simple container path
                if ':' in volume or volume.startswith('/'):
                    cmd.extend(["-v", volume])
                else:
                    log(f"Warning: Skipping invalid volume syntax: '{volume}'. Volume must be 'host:container' or '/container/path'")
        
        if environment:
            for key, value in environment.items():
                cmd.extend(["-e", f"{key}={value}"])
        
        if extra_args:
            cmd.extend(extra_args)
        
        cmd.append(image)
        
        # Use shlex.quote to properly quote arguments that contain spaces
        quoted_cmd = [shlex.quote(arg) for arg in cmd]
        cmd_str = " ".join(quoted_cmd)
        return CommandExecuter.run_cmd(cmd_str, server_ip, user)

    @staticmethod
    def stop_container(container_name: str, server_ip: str = 'localhost', 
                      user: str = "root", ignore_if_not_exists: bool = True) -> Any:
        """Stop Docker container, optionally ignoring if it doesn't exist"""
        try:
            return CommandExecuter.run_cmd(["docker", "stop", container_name], server_ip, user)
        except Exception as e:
            error_msg = str(e).lower()
            if ignore_if_not_exists and ("no such container" in error_msg or "not found" in error_msg):
                log(f"Container '{container_name}' not found (already stopped or doesn't exist)")
                return None
            raise

    @staticmethod
    def remove_container(container_name: str, server_ip: str = 'localhost', 
                        user: str = "root", force: bool = False, 
                        ignore_if_not_exists: bool = True) -> Any:
        """Remove Docker container, optionally ignoring if it doesn't exist"""
        cmd = ["docker", "rm"]
        if force:
            cmd.append("-f")
        cmd.append(container_name)
        
        try:
            return CommandExecuter.run_cmd(cmd, server_ip, user)
        except Exception as e:
            error_msg = str(e).lower()
            if ignore_if_not_exists and ("no such container" in error_msg or "not found" in error_msg):
                log(f"Container '{container_name}' not found (already removed or doesn't exist)")
                return None
            raise

    @staticmethod
    def stop_and_remove_container(container_name: str, server_ip: str = 'localhost', 
                                 user: str = "root", ignore_if_not_exists: bool = True) -> List[Any]:
        """Stop and remove container in sequence"""
        results = []
        
        stop_result = DockerExecuter.stop_container(
            container_name, server_ip, user, ignore_if_not_exists
        )
        results.append(stop_result)
        
        remove_result = DockerExecuter.remove_container(
            container_name, server_ip, user, force=True, ignore_if_not_exists=ignore_if_not_exists
        )
        results.append(remove_result)
        
        return results

    @staticmethod
    def create_network(network_name: str, server_ip: str = 'localhost', 
                      user: str = "root", ignore_if_exists: bool = True) -> Any:
        """Create Docker network, optionally ignoring if it already exists"""
        try:
            return CommandExecuter.run_cmd(["docker", "network", "create", network_name], server_ip, user)
        except Exception as e:
            error_msg = str(e).lower()
            if ignore_if_exists and "already exists" in error_msg:
                log(f"Network '{network_name}' already exists")
                return None
            raise

    @staticmethod
    def network_exists(network_name: str, server_ip: str = 'localhost', 
                      user: str = "root") -> bool:
        """Check if Docker network exists"""
        try:
            result = CommandExecuter.run_cmd([
                "docker", "network", "ls", "--filter", f"name={network_name}", 
                "--format={{.Name}}"
            ], server_ip, user)
            
            if isinstance(result, subprocess.CompletedProcess):
                return network_name in result.stdout
            else:
                return network_name in str(result)
        except:
            return False

    @staticmethod
    def create_volume(volume_name: str, server_ip: str = 'localhost', 
                     user: str = "root", ignore_if_exists: bool = True) -> Any:
        """Create Docker volume, optionally ignoring if it already exists"""
        try:
            return CommandExecuter.run_cmd(["docker", "volume", "create", volume_name], server_ip, user)
        except Exception as e:
            error_msg = str(e).lower()
            if ignore_if_exists and ("already exists" in error_msg or "volume name" in error_msg):
                log(f"Volume '{volume_name}' already exists")
                return None
            raise

    @staticmethod
    def volume_exists(volume_name: str, server_ip: str = 'localhost', 
                     user: str = "root") -> bool:
        """Check if Docker volume exists"""
        try:
            result = CommandExecuter.run_cmd([
                "docker", "volume", "ls", "--filter", f"name={volume_name}", 
                "--format={{.Name}}"
            ], server_ip, user)
            
            if isinstance(result, subprocess.CompletedProcess):
                return volume_name in result.stdout
            else:
                return volume_name in str(result)
        except:
            return False

    @staticmethod
    def container_exists(container_name: str, server_ip: str = 'localhost', 
                        user: str = "root") -> bool:
        """Check if Docker container exists"""
        try:
            result = CommandExecuter.run_cmd([
                "docker", "ps", "-a", "--filter", f"name={container_name}", 
                "--format={{.Names}}"
            ], server_ip, user)
            
            if isinstance(result, subprocess.CompletedProcess):
                return container_name in result.stdout
            else:
                return container_name in str(result)
        except:
            return False

    @staticmethod
    def is_container_running(container_name: str, server_ip: str = 'localhost', 
                           user: str = "root") -> bool:
        """Check if Docker container is running"""
        try:
            result = CommandExecuter.run_cmd([
                "docker", "ps", "--filter", f"name={container_name}", 
                "--format={{.Names}}"
            ], server_ip, user)
            
            if isinstance(result, subprocess.CompletedProcess):
                return container_name in result.stdout
            else:
                return container_name in str(result)
        except:
            return False

    @staticmethod
    def get_container_logs(container_name: str, lines: int = 100, 
                          server_ip: str = 'localhost', user: str = "root") -> str:
        """Get Docker container logs"""
        cmd = ["docker", "logs", "--tail", str(lines), container_name]
        result = CommandExecuter.run_cmd(cmd, server_ip, user)
        
        if isinstance(result, subprocess.CompletedProcess):
            return result.stdout
        else:
            return str(result)

    @staticmethod
    def pull_image(image: str, server_ip: str = 'localhost', user: str = "root") -> Any:
        """Pull Docker image - servers need this to get images from registry"""
        return CommandExecuter.run_cmd(["docker", "pull", image], server_ip, user)

    @staticmethod
    def push_image(image: str) -> Any:
        """Push Docker image from local Docker to registry"""
        return CommandExecuter.run_cmd(["docker", "push", image])

    @staticmethod
    def exec_in_container(container_name: str, command: Union[str, List[str]], 
                         interactive: bool = False, server_ip: str = 'localhost', 
                         user: str = "root") -> Any:
        """Execute command inside running container"""
        cmd = ["docker", "exec"]
        
        if interactive:
            cmd.append("-it")
        
        cmd.append(container_name)
        
        if isinstance(command, str):
            cmd.extend(command.split())
        else:
            cmd.extend(command)
        
        return CommandExecuter.run_cmd(cmd, server_ip, user)

    @staticmethod
    def mkdir_on_server(directory: str, server_ip: str = 'localhost', user: str = "root") -> Any:
        """Create directory on server (helper for volume mounting) with cross-platform support"""
        if server_ip == 'localhost' or server_ip is None:
            # Local operation - detect platform
            if platform.system() == "Windows":
                # Use PowerShell on Windows to handle path creation reliably
                return CommandExecuter.run_cmd(f'powershell -Command "New-Item -ItemType Directory -Force -Path \\"{directory}\\""', server_ip, user)
            else:
                # Unix-like systems
                return CommandExecuter.run_cmd(f'mkdir -p {directory}', server_ip, user)
        else:
            # Remote operation - assume Unix-like
            return CommandExecuter.run_cmd(f'mkdir -p {directory}', server_ip, user)
        
    @staticmethod
    def run_container_once(
        image: str,
        command: List[str],
        ports: Optional[List[str]] = None,
        volumes: Optional[List[str]] = None,
        environment: Optional[Dict[str, str]] = None,
        network: Optional[str] = None,
        server_ip: str = 'localhost',
        user: str = "root"
    ) -> Any:
        """Run container, wait for completion, and remove it"""
        name = f"temp_{int(time.time())}_{os.urandom(4).hex()}"
        
        cmd = ["docker", "run", "--rm", "--name", name]
        if network:
            cmd.extend(["--network", network])
        if ports:
            for port in ports:
                cmd.extend(["-p", port])
        if volumes:
            for vol in volumes:
                cmd.extend(["-v", vol])
        if environment:
            for k, v in environment.items():
                cmd.extend(["-e", f"{k}={v}"])
        cmd.append(image)
        cmd.extend(command)
        
        return CommandExecuter.run_cmd(cmd, server_ip, user)