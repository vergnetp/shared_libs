import subprocess
import shlex
import time
import os
import re
import platform
from typing import Union, List, Optional, Dict, Any

try:
    from .execute_cmd import CommandExecuter
except ImportError:
    from execute_cmd import CommandExecuter
try:
    from .logger import Logger
except ImportError:
    from logger import Logger
try:
    from .deployment_naming import DeploymentNaming
except ImportError:
    from deployment_naming import DeploymentNaming


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
    
    @staticmethod
    def get_published_ports(container_name: str, server_ip: str = "localhost", user: str = "root") -> Dict[str, str]:
        """Get port mappings: container_port -> host_port"""
        cmd = f'docker port {container_name}'
        result = CommandExecuter.run_cmd(cmd, server_ip, user)
        
        # Parse output like: "5432/tcp -> 0.0.0.0:8357"
        port_map = {}
        for line in str(result).splitlines():
            if '->' in line:
                container_port, host_binding = line.split('->')
                container_port = container_port.strip()
                host_port = host_binding.strip().split(':')[-1]  # Extract port from "0.0.0.0:8357"
                port_map[container_port] = host_port
        
        return port_map
    
    @staticmethod
    def find_containers_by_pattern(
        pattern: str,
        server_ip: str = 'localhost',
        user: str = "root"
    ) -> List[Dict[str, Any]]:
        """
        Find containers matching a name pattern.
        
        Useful for finding both primary and secondary containers during toggle deployments.
        
        Args:
            pattern: Container name pattern (e.g., "myproj_dev_api*")
            server_ip: Target server IP
            user: SSH user
            
        Returns:
            List of dicts: [{"name": "container_name", "port": "8357"}, ...]
            
        Examples:
            find_containers_by_pattern("new_project_uat_postgres*", "localhost")
            Returns:
            [
                {"name": "new_project_uat_postgres", "port": "8357"},
                {"name": "new_project_uat_postgres_secondary", "port": "18357"}
            ]
        """
        try:
            # List all containers matching pattern
            cmd = f'docker ps -a --filter "name={pattern}" --format "{{{{.Names}}}}"'
            result = CommandExecuter.run_cmd(cmd, server_ip, user)
            
            if isinstance(result, subprocess.CompletedProcess):
                container_names = result.stdout.strip().split('\n')
            else:
                container_names = str(result).strip().split('\n')
            
            container_names = [name.strip() for name in container_names if name.strip()]
            
            if not container_names:
                return []
            
            # Get port info for each container
            containers = []
            for name in container_names:
                # Get published ports
                port_map = DockerExecuter.get_published_ports(name, server_ip, user)
                
                # Extract first host port (if any)
                host_port = None
                if port_map:
                    # Get first port mapping value
                    host_port = list(port_map.values())[0] if port_map else None
                
                containers.append({
                    "name": name,
                    "port": host_port
                })
            
            return containers
            
        except Exception as e:
            log(f"Error finding containers by pattern '{pattern}': {e}")
            return []


    @staticmethod
    def find_service_container(
        project: str,
        env: str,
        service: str,
        server_ip: str,
        user: str = "root"
    ) -> Optional[Dict[str, Any]]:
        """
        Find existing container for a service (primary or secondary).
        
        Used during toggle deployment to determine which container is currently running.
        
        Args:
            project: Project name
            env: Environment
            service: Service name
            server_ip: Target server IP
            user: SSH user
            
        Returns:
            {"name": "container_name", "port": 8357} or None if not found
            
        Examples:
            find_service_container("myproj", "dev", "postgres", "localhost")
            Returns: {"name": "myproj_dev_postgres", "port": 8357}
            
            Or if secondary is running:
            Returns: {"name": "myproj_dev_postgres_secondary", "port": 18357}
            
            Or if nothing running:
            Returns: None
        """       
        # Get base container name
        base_name = DeploymentNaming.get_container_name(project, env, service)
        
        # Check for primary container
        if DockerExecuter.is_container_running(base_name, server_ip, user):
            # Get port info
            port_map = DockerExecuter.get_published_ports(base_name, server_ip, user)
            host_port = list(port_map.values())[0] if port_map else None
            
            return {
                "name": base_name,
                "port": host_port
            }
        
        # Check for secondary container
        secondary_name = f"{base_name}_secondary"
        if DockerExecuter.is_container_running(secondary_name, server_ip, user):
            # Get port info
            port_map = DockerExecuter.get_published_ports(secondary_name, server_ip, user)
            host_port = list(port_map.values())[0] if port_map else None
            
            return {
                "name": secondary_name,
                "port": host_port
            }
        
        # Nothing running
        return None


    @staticmethod
    def get_published_ports(
        container_name: str,
        server_ip: str = "localhost",
        user: str = "root"
    ) -> Dict[str, str]:
        """
        Get port mappings for a container: container_port -> host_port
        
        Args:
            container_name: Container name
            server_ip: Target server IP
            user: SSH user
            
        Returns:
            Dict mapping container port to host port
            
        Examples:
            get_published_ports("myproj_dev_postgres", "localhost")
            Returns: {"5432/tcp": "8357"}
            
            get_published_ports("myproj_dev_api", "10.0.0.1")
            Returns: {"8000/tcp": "8412"}
            
            For containers without port mapping (single-server mode):
            Returns: {}
        """
        try:
            cmd = f'docker port {container_name}'
            result = CommandExecuter.run_cmd(cmd, server_ip, user)
            
            # Parse output like: "5432/tcp -> 0.0.0.0:8357"
            port_map = {}
            
            output = str(result)
            if isinstance(result, subprocess.CompletedProcess):
                output = result.stdout
            
            for line in output.splitlines():
                line = line.strip()
                if '->' in line:
                    container_port, host_binding = line.split('->')
                    container_port = container_port.strip()
                    host_port = host_binding.strip().split(':')[-1]  # Extract port from "0.0.0.0:8357"
                    port_map[container_port] = host_port
            
            return port_map
            
        except Exception as e:
            # Container might not have any port mappings (single-server mode)
            # This is not an error - return empty dict
            return {}
        
    @staticmethod
    def get_container_exit_code(
        container_name: str, 
        server_ip: str = 'localhost', 
        user: str = "root"
    ) -> int:
        """
        Get exit code of a container (running or stopped).
        
        Args:
            container_name: Container name
            server_ip: Target server IP
            user: SSH user
            
        Returns:
            Exit code:
            - 0 = Success (normal completion)
            - 1-255 = Error/crash
            - -1 = Could not determine (container doesn't exist)
        """
        try:
            cmd = f'docker inspect {container_name} --format="{{{{.State.ExitCode}}}}"'
            result = CommandExecuter.run_cmd(cmd, server_ip, user)
            
            if isinstance(result, subprocess.CompletedProcess):
                exit_code_str = result.stdout.strip()
            else:
                exit_code_str = str(result).strip()
            
            # Clean the output - sometimes Docker inspect returns extra text
            # Extract just the numeric exit code            
            match = re.search(r'^\d+$', exit_code_str)
            if match:
                exit_code = int(match.group())
            else:
                # Try to find a number anywhere in the output
                match = re.search(r'\d+', exit_code_str)
                if match:
                    exit_code = int(match.group())
                else:
                    log(f"Could not parse exit code from: {exit_code_str}")
                    exit_code = -1
            
            return exit_code
            
        except Exception as e:
            log(f"Could not get exit code for {container_name}: {e}")
            return -1