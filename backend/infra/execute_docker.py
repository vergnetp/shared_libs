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


def get_agent():
    try:
        from .health_monitor import HealthMonitor
    except ImportError:
        from health_monitor import HealthMonitor
    return HealthMonitor

class DockerExecuter:
    """
    Docker-specific command executor with specialized methods.
    
    MIGRATION COMPLETE: Now uses HTTP agent instead of SSH for remote operations.
    API remains unchanged - all existing code works without modification.
    
    Architecture:
    - localhost operations: Direct subprocess calls (unchanged)
    - Remote operations: HTTP agent calls (no more SSH!)
    """

    _network_cache = {}
    
    # Feature flag for gradual rollout
    USE_AGENT = True

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
        """
        Run Docker container with comprehensive parameters.
        
        MIGRATED: Uses HTTP agent for remote operations.
        """
        # Localhost - use direct docker commands
        if server_ip == 'localhost' or server_ip is None:
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
                    if ':' in volume or volume.startswith('/'):
                        cmd.extend(["-v", volume])
                    else:
                        log(f"Warning: Skipping invalid volume syntax: '{volume}'")
            
            if environment:
                for key, value in environment.items():
                    cmd.extend(["-e", f"{key}={value}"])
            
            if extra_args:
                cmd.extend(extra_args)
            
            cmd.append(image)
            
            quoted_cmd = [shlex.quote(arg) for arg in cmd]
            cmd_str = " ".join(quoted_cmd)
            return CommandExecuter.run_cmd(cmd_str)
        
        # Remote - use HTTP agent
        if DockerExecuter.USE_AGENT:
            try:
                payload = {
                    'name': name,
                    'image': image,
                    'ports': ports or {},
                    'volumes': volumes or [],
                    'env_vars': environment or {},
                    'restart_policy': restart_policy
                }
                
                if network:
                    payload['network'] = network
                
                response = get_agent().agent_request(
                    server_ip,
                    "POST",
                    "/containers/run",
                    json_data=payload,
                    timeout=60
                )
                
                if response.get('status') == 'started':
                    return response.get('container_id')
                else:
                    raise Exception(f"Container start failed: {response.get('error', 'unknown')}")
                    
            except Exception as e:
                log(f"Agent call failed, falling back to SSH: {e}")
                # Fallback to SSH if agent fails
                pass
        
        # Fallback to SSH (old behavior)
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
                if ':' in volume or volume.startswith('/'):
                    cmd.extend(["-v", volume])
        if environment:
            for key, value in environment.items():
                cmd.extend(["-e", f"{key}={value}"])
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(image)
        
        quoted_cmd = [shlex.quote(arg) for arg in cmd]
        cmd_str = " ".join(quoted_cmd)
        return CommandExecuter.run_cmd(cmd_str, server_ip, user)

    @staticmethod
    def stop_container(container_name: str, server_ip: str = 'localhost', 
                      user: str = "root", ignore_if_not_exists: bool = True) -> Any:
        """
        Stop Docker container, optionally ignoring if it doesn't exist.
        
        MIGRATED: Uses HTTP agent for remote operations.
        """
        # Localhost - use direct docker commands
        if server_ip == 'localhost' or server_ip is None:
            try:
                return CommandExecuter.run_cmd(["docker", "stop", container_name])
            except Exception as e:
                error_msg = str(e).lower()
                if ignore_if_not_exists and ("no such container" in error_msg or "not found" in error_msg):
                    log(f"Container '{container_name}' not found (already stopped or doesn't exist)")
                    return None
                raise
        
        # Remote - use HTTP agent
        if DockerExecuter.USE_AGENT:
            try:
                response = get_agent().agent_request(
                    server_ip,
                    "POST",
                    f"/containers/{container_name}/stop",
                    timeout=30
                )
                
                if response.get('status') == 'stopped':
                    return True
                else:
                    error = response.get('error', 'unknown')
                    if ignore_if_not_exists and 'not found' in error.lower():
                        log(f"Container '{container_name}' not found (already stopped)")
                        return None
                    raise Exception(f"Stop failed: {error}")
                    
            except Exception as e:
                error_msg = str(e).lower()
                if ignore_if_not_exists and 'not found' in error_msg:
                    log(f"Container '{container_name}' not found (already stopped)")
                    return None
                log(f"Agent call failed, falling back to SSH: {e}")
                # Fallback to SSH
                pass
        
        # Fallback to SSH (old behavior)
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
        """
        Remove Docker container, optionally ignoring if it doesn't exist.
        
        MIGRATED: Uses HTTP agent for remote operations.
        """
        # Localhost - use direct docker commands
        if server_ip == 'localhost' or server_ip is None:
            cmd = ["docker", "rm"]
            if force:
                cmd.append("-f")
            cmd.append(container_name)
            
            try:
                return CommandExecuter.run_cmd(cmd)
            except Exception as e:
                error_msg = str(e).lower()
                if ignore_if_not_exists and ("no such container" in error_msg or "not found" in error_msg):
                    log(f"Container '{container_name}' not found (already removed or doesn't exist)")
                    return None
                raise
        
        # Remote - use HTTP agent
        if DockerExecuter.USE_AGENT:
            try:
                response = get_agent().agent_request(
                    server_ip,
                    "POST",
                    f"/containers/{container_name}/remove",
                    timeout=30
                )
                
                if response.get('status') == 'removed':
                    return True
                else:
                    error = response.get('error', 'unknown')
                    if ignore_if_not_exists and 'not found' in error.lower():
                        log(f"Container '{container_name}' not found (already removed)")
                        return None
                    raise Exception(f"Remove failed: {error}")
                    
            except Exception as e:
                error_msg = str(e).lower()
                if ignore_if_not_exists and 'not found' in error_msg:
                    log(f"Container '{container_name}' not found (already removed)")
                    return None
                log(f"Agent call failed, falling back to SSH: {e}")
                # Fallback to SSH
                pass
        
        # Fallback to SSH (old behavior)
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
        """
        Stop and remove container in sequence.
        
        MIGRATED: Uses HTTP agent for remote operations.
        """
        results = []
        
        stop_result = DockerExecuter.stop_container(
            container_name, server_ip, user, ignore_if_not_exists
        )
        results.append(stop_result)
        
        # Wait a moment for container to fully stop
        if server_ip != 'localhost' and server_ip is not None:
            time.sleep(1)
        
        remove_result = DockerExecuter.remove_container(
            container_name, server_ip, user, force=True, ignore_if_not_exists=ignore_if_not_exists
        )
        results.append(remove_result)
        
        return results

    @staticmethod
    def create_network(network_name: str, server_ip: str = 'localhost', 
                    user: str = "root", ignore_if_exists: bool = True) -> Any:
        """Create Docker network with caching"""
        
        # Check cache first
        cache_key = server_ip or 'localhost'
        if cache_key in DockerExecuter._network_cache:
            if network_name in DockerExecuter._network_cache[cache_key]:
                log(f"Network '{network_name}' already exists (cached)")
                return None
        
        # Not in cache - create (still uses SSH for now - less critical)
        try:
            result = CommandExecuter.run_cmd(["docker", "network", "create", network_name], server_ip, user)
            
            # Cache it
            if cache_key not in DockerExecuter._network_cache:
                DockerExecuter._network_cache[cache_key] = {}
            DockerExecuter._network_cache[cache_key][network_name] = True
            
            return result
            
        except Exception as e:
            error_msg = str(e).lower()
            if ignore_if_exists and "already exists" in error_msg:
                # Cache it
                if cache_key not in DockerExecuter._network_cache:
                    DockerExecuter._network_cache[cache_key] = {}
                DockerExecuter._network_cache[cache_key][network_name] = True
                
                log(f"Network '{network_name}' already exists (cached)")
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
    def get_container_logs(container_name: str, lines: int = 100, 
                          server_ip: str = 'localhost', user: str = "root") -> str:
        """
        Get Docker container logs.
        
        MIGRATED: Uses HTTP agent for remote operations.
        """
        # Localhost - use direct docker commands
        if server_ip == 'localhost' or server_ip is None:
            cmd = ["docker", "logs", "--tail", str(lines), container_name]
            result = CommandExecuter.run_cmd(cmd)
            
            if isinstance(result, subprocess.CompletedProcess):
                return result.stdout
            else:
                return str(result)
        
        # Remote - use HTTP agent
        if DockerExecuter.USE_AGENT:
            try:
                response = get_agent().agent_request(
                    server_ip,
                    "GET",
                    f"/containers/{container_name}/logs?lines={lines}",
                    timeout=30
                )
                
                return response.get('logs', '')
                
            except Exception as e:
                log(f"Agent call failed for logs, falling back to SSH: {e}")
                # Fallback to SSH
                pass
        
        # Fallback to SSH (old behavior)
        cmd = ["docker", "logs", "--tail", str(lines), container_name]
        result = CommandExecuter.run_cmd(cmd, server_ip, user)
        
        if isinstance(result, subprocess.CompletedProcess):
            return result.stdout
        else:
            return str(result)

    @staticmethod
    def pull_image(image: str, server_ip: str = 'localhost', user: str = "root") -> Any:
        """
        Pull Docker image - servers need this to get images from registry.
        
        MIGRATED: Uses HTTP agent for remote operations.
        """
        # Localhost - use direct docker commands
        if server_ip == 'localhost' or server_ip is None:
            return CommandExecuter.run_cmd(["docker", "pull", image])
        
        # Remote - use HTTP agent
        if DockerExecuter.USE_AGENT:
            try:
                response = get_agent().agent_request(
                    server_ip,
                    "POST",
                    f"/images/{image}/pull",
                    timeout=600  # 10 minutes for large images
                )
                
                if response.get('status') == 'pulled':
                    return True
                else:
                    raise Exception(f"Image pull failed: {response.get('error', 'unknown')}")
                    
            except Exception as e:
                log(f"Agent call failed for pull, falling back to SSH: {e}")
                # Fallback to SSH
                pass
        
        # Fallback to SSH (old behavior)
        return CommandExecuter.run_cmd(["docker", "pull", image], server_ip, user)

    @staticmethod
    def push_image(image: str) -> Any:
        """Push Docker image from local Docker to registry"""
        return CommandExecuter.run_cmd(["docker", "push", image])

    @staticmethod
    def exec_in_container(container_name: str, command: Union[str, List[str]], 
                         interactive: bool = False, server_ip: str = 'localhost', 
                         user: str = "root") -> Any:
        """Execute command inside running container (still uses SSH for remote)"""
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
                return CommandExecuter.run_cmd(f'powershell -Command "New-Item -ItemType Directory -Force -Path \\"{directory}\\""')
            else:
                return CommandExecuter.run_cmd(f'mkdir -p {directory}')
        else:
            # Remote operation - assume Unix-like (still uses SSH)
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
        """Run container, wait for completion, and remove it (still uses SSH for remote)"""
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
        """Get port mappings: container_port -> host_port (still uses SSH for remote)"""
        cmd = f'docker port {container_name}'
        result = CommandExecuter.run_cmd(cmd, server_ip, user)
        
        # Parse output like: "5432/tcp -> 0.0.0.0:8357"
        port_map = {}
        for line in str(result).splitlines():
            if '->' in line:
                container_port, host_binding = line.split('->')
                container_port = container_port.strip()
                host_port = host_binding.strip().split(':')[-1]
                port_map[container_port] = host_port
        
        return port_map
    
    @staticmethod
    def find_containers_by_pattern(
        pattern: str,
        server_ip: str = 'localhost',
        user: str = "root"
    ) -> List[Dict[str, Any]]:
        """Find containers matching a name pattern (still uses SSH for remote)"""
        cmd = f"docker ps -a --filter 'name={pattern}' --format '{{{{.ID}}}}|{{{{.Names}}}}|{{{{.Status}}}}|{{{{.Image}}}}'"
        
        result = CommandExecuter.run_cmd(cmd, server_ip, user)
        output = result.stdout if hasattr(result, 'stdout') else str(result)
        
        containers = []
        for line in output.strip().split('\n'):
            if line:
                parts = line.split('|')
                if len(parts) >= 4:
                    containers.append({
                        'id': parts[0],
                        'name': parts[1],
                        'status': parts[2],
                        'image': parts[3]
                    })
        
        return containers
    


    @staticmethod
    def find_service_container(user, project, env, service, server_ip='localhost'):
        """Find container for service"""
        base_name = DeploymentNaming.get_container_name(user, project, env, service)
        patterns = [base_name, f"{base_name}_secondary"]
        
        for pattern in patterns:
            result = CommandExecuter.run_cmd(
                f"docker ps -a --filter 'name=^{pattern}$' "
                f"--format '{{{{.Names}}}}|{{{{.Ports}}}}|{{{{.Status}}}}'",
                server_ip, "root"
            )
            output = str(result).strip() if result else ""
            if output:
                parts = output.split('|')
                port = None
                if len(parts) > 1:
                    import re
                    match = re.search(r':(\d+)->', parts[1])
                    if match:
                        port = int(match.group(1))
                return {'name': parts[0], 'port': port, 'status': parts[2] if len(parts) > 2 else 'unknown'}
        return None

    @staticmethod
    def is_container_running(container_name, server_ip='localhost'):
        """Check if container is running"""
        result = CommandExecuter.run_cmd(
            f"docker ps --filter 'name=^{container_name}$' --format '{{{{.Status}}}}'",
            server_ip, "root"
        )
        output = str(result).strip() if result else ""
        return output and 'Up' in output
    

    @staticmethod
    def container_exists(container_name: str, server_ip: str = 'localhost') -> bool:
        """Check if container exists (running or stopped)"""
        try:
            result = CommandExecuter.run_cmd(
                f"docker ps -a --filter 'name=^{container_name}$' --format '{{{{.Names}}}}'",
                server_ip, "root"
            )
            output = str(result).strip() if result else ""
            return bool(output and container_name in output)
        except:
            return False

    @staticmethod
    def volume_exists(volume_name: str, server_ip: str = 'localhost', user='todo') -> bool:
        """Check if Docker volume exists"""
        try:
            result = CommandExecuter.run_cmd(
                f"docker volume ls --filter 'name=^{volume_name}$' --format '{{{{.Name}}}}'",
                server_ip, "root"
            )
            output = str(result).strip() if result else ""
            return bool(output and volume_name in output)
        except:
            return False