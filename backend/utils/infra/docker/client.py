"""
Docker Client - Container operations.

Clean interface for Docker operations. Can run locally or via SSH on remote servers.
"""

from __future__ import annotations
import json
import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Any, List, Optional, Tuple

if TYPE_CHECKING:
    from ..context import DeploymentContext
    from .service import Service

from ..core.result import Result, ContainerResult


@dataclass
class Container:
    """Running container info."""
    id: str
    name: str
    image: str
    status: str
    ports: Dict[str, int]  # container_port -> host_port
    created: str
    
    @property
    def is_running(self) -> bool:
        return "running" in self.status.lower()


class DockerClient:
    """
    Docker operations client.
    
    Executes Docker commands locally or on remote servers via SSH.
    
    Usage:
        docker = DockerClient(ctx)
        
        # Run container locally
        result = docker.run("nginx:latest", name="web", ports={80: 8080})
        
        # Run on remote server
        result = docker.run("nginx:latest", name="web", server="1.2.3.4")
        
        # List containers
        containers = docker.ps()
        
        # Stop and remove
        docker.stop("web")
        docker.rm("web")
    """
    
    def __init__(self, ctx: 'DeploymentContext', ssh_client: Optional[Any] = None):
        """
        Initialize Docker client.
        
        Args:
            ctx: Deployment context
            ssh_client: Optional SSH client for remote operations
        """
        self.ctx = ctx
        self.ssh = ssh_client
    
    # =========================================================================
    # Command Execution
    # =========================================================================
    
    def _exec(
        self, 
        cmd: str, 
        server: Optional[str] = None,
        capture: bool = True,
    ) -> Tuple[int, str, str]:
        """
        Execute command locally or remotely.
        
        Args:
            cmd: Command to execute
            server: Remote server IP (None = local)
            capture: Capture output
            
        Returns:
            Tuple of (return_code, stdout, stderr)
        """
        if server and server != "localhost":
            # Remote execution via SSH
            if self.ssh:
                return self.ssh.exec(server, cmd)
            else:
                # Fallback to subprocess ssh
                import subprocess
                full_cmd = f"ssh -o StrictHostKeyChecking=no root@{server} {shlex.quote(cmd)}"
                result = subprocess.run(
                    full_cmd, 
                    shell=True, 
                    capture_output=capture,
                    text=True,
                )
                return result.returncode, result.stdout, result.stderr
        else:
            # Local execution
            import subprocess
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=capture,
                text=True,
            )
            return result.returncode, result.stdout, result.stderr
    
    def _docker(
        self, 
        args: str, 
        server: Optional[str] = None,
    ) -> Tuple[int, str, str]:
        """Execute docker command."""
        return self._exec(f"docker {args}", server)
    
    # =========================================================================
    # Container Operations
    # =========================================================================
    
    def run(
        self,
        image: str,
        name: str,
        server: Optional[str] = None,
        ports: Optional[Dict[int, int]] = None,
        environment: Optional[Dict[str, str]] = None,
        volumes: Optional[Dict[str, str]] = None,
        network: Optional[str] = None,
        command: Optional[str] = None,
        entrypoint: Optional[str] = None,
        memory: Optional[str] = None,
        cpus: Optional[float] = None,
        restart: str = "unless-stopped",
        labels: Optional[Dict[str, str]] = None,
        detach: bool = True,
        remove_existing: bool = True,
    ) -> ContainerResult:
        """
        Run a container.
        
        Args:
            image: Docker image
            name: Container name
            server: Server IP (None = local)
            ports: Port mappings {container_port: host_port}
            environment: Environment variables
            volumes: Volume mounts {host_path: container_path}
            network: Docker network to join
            command: Override CMD
            entrypoint: Override ENTRYPOINT
            memory: Memory limit (e.g., "512m")
            cpus: CPU limit
            restart: Restart policy
            labels: Container labels
            detach: Run in background
            remove_existing: Remove existing container with same name
            
        Returns:
            ContainerResult
        """
        # Remove existing container if requested
        if remove_existing:
            self.rm(name, server=server, force=True)
        
        # Build docker run command
        args = ["run"]
        
        if detach:
            args.append("-d")
        
        args.extend(["--name", name])
        
        if restart:
            args.extend(["--restart", restart])
        
        if network:
            args.extend(["--network", network])
        
        if memory:
            args.extend(["--memory", memory])
        
        if cpus:
            args.extend(["--cpus", str(cpus)])
        
        # Ports
        if ports:
            for container_port, host_port in ports.items():
                args.extend(["-p", f"{host_port}:{container_port}"])
        
        # Environment
        if environment:
            for key, value in environment.items():
                args.extend(["-e", f"{key}={value}"])
        
        # Volumes
        if volumes:
            for host_path, container_path in volumes.items():
                args.extend(["-v", f"{host_path}:{container_path}"])
        
        # Labels
        if labels:
            for key, value in labels.items():
                args.extend(["--label", f"{key}={value}"])
        
        if entrypoint:
            args.extend(["--entrypoint", entrypoint])
        
        # Image
        args.append(image)
        
        # Command
        if command:
            args.append(command)
        
        cmd = " ".join(shlex.quote(a) for a in args)
        code, stdout, stderr = self._docker(cmd, server)
        
        if code == 0:
            container_id = stdout.strip()[:12]
            return ContainerResult(
                success=True,
                message=f"Container {name} started",
                container_id=container_id,
                container_name=name,
                server_ip=server or "localhost",
                port=list(ports.values())[0] if ports else None,
            )
        else:
            return ContainerResult(
                success=False,
                error=stderr.strip() or stdout.strip(),
                container_name=name,
                server_ip=server or "localhost",
            )
    
    def stop(
        self, 
        name: str, 
        server: Optional[str] = None,
        timeout: int = 10,
    ) -> Result:
        """Stop a container."""
        code, stdout, stderr = self._docker(f"stop -t {timeout} {name}", server)
        
        if code == 0:
            return Result.ok(f"Container {name} stopped")
        else:
            return Result.fail(stderr.strip() or f"Failed to stop {name}")
    
    def rm(
        self, 
        name: str, 
        server: Optional[str] = None,
        force: bool = False,
        volumes: bool = False,
    ) -> Result:
        """Remove a container."""
        args = ["rm"]
        if force:
            args.append("-f")
        if volumes:
            args.append("-v")
        args.append(name)
        
        code, stdout, stderr = self._docker(" ".join(args), server)
        
        # Ignore "not found" errors
        if code == 0 or "No such container" in stderr or "not found" in stderr.lower():
            return Result.ok(f"Container {name} removed")
        else:
            return Result.fail(stderr.strip())
    
    def logs(
        self,
        name: str,
        server: Optional[str] = None,
        lines: int = 100,
        follow: bool = False,
    ) -> str:
        """Get container logs."""
        args = f"logs --tail {lines}"
        if follow:
            args += " -f"
        args += f" {name}"
        
        code, stdout, stderr = self._docker(args, server)
        return stdout + stderr
    
    def exec(
        self,
        name: str,
        command: str,
        server: Optional[str] = None,
        interactive: bool = False,
    ) -> Tuple[int, str]:
        """Execute command in running container."""
        args = "exec"
        if interactive:
            args += " -it"
        args += f" {name} {command}"
        
        code, stdout, stderr = self._docker(args, server)
        return code, stdout + stderr
    
    # =========================================================================
    # Container Queries
    # =========================================================================
    
    def ps(
        self,
        server: Optional[str] = None,
        all: bool = False,
        filter_name: Optional[str] = None,
    ) -> List[Container]:
        """
        List containers.
        
        Args:
            server: Server IP
            all: Include stopped containers
            filter_name: Filter by name pattern
            
        Returns:
            List of Container objects
        """
        args = "ps --format '{{json .}}'"
        if all:
            args = "ps -a --format '{{json .}}'"
        if filter_name:
            args += f" --filter name={filter_name}"
        
        code, stdout, stderr = self._docker(args, server)
        
        if code != 0:
            return []
        
        containers = []
        for line in stdout.strip().split("\n"):
            if not line:
                continue
            try:
                data = json.loads(line)
                containers.append(Container(
                    id=data.get("ID", ""),
                    name=data.get("Names", ""),
                    image=data.get("Image", ""),
                    status=data.get("Status", ""),
                    ports=self._parse_ports(data.get("Ports", "")),
                    created=data.get("CreatedAt", ""),
                ))
            except json.JSONDecodeError:
                continue
        
        return containers
    
    def inspect(
        self,
        name: str,
        server: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get container details."""
        code, stdout, stderr = self._docker(f"inspect {name}", server)
        
        if code != 0:
            return None
        
        try:
            data = json.loads(stdout)
            return data[0] if data else None
        except json.JSONDecodeError:
            return None
    
    def exists(
        self,
        name: str,
        server: Optional[str] = None,
    ) -> bool:
        """Check if container exists."""
        return self.inspect(name, server) is not None
    
    def is_running(
        self,
        name: str,
        server: Optional[str] = None,
    ) -> bool:
        """Check if container is running."""
        info = self.inspect(name, server)
        if not info:
            return False
        return info.get("State", {}).get("Running", False)
    
    # =========================================================================
    # Image Operations
    # =========================================================================
    
    def pull(
        self,
        image: str,
        server: Optional[str] = None,
    ) -> Result:
        """Pull an image."""
        code, stdout, stderr = self._docker(f"pull {image}", server)
        
        if code == 0:
            return Result.ok(f"Pulled {image}")
        else:
            return Result.fail(stderr.strip())
    
    def push(
        self,
        image: str,
        server: Optional[str] = None,
    ) -> Result:
        """Push an image to registry."""
        code, stdout, stderr = self._docker(f"push {image}", server)
        
        if code == 0:
            return Result.ok(f"Pushed {image}")
        else:
            return Result.fail(stderr.strip())
    
    def build(
        self,
        tag: str,
        dockerfile: str = "Dockerfile",
        context: str = ".",
        server: Optional[str] = None,
        build_args: Optional[Dict[str, str]] = None,
        no_cache: bool = False,
    ) -> Result:
        """Build an image."""
        args = ["build", "-t", tag, "-f", dockerfile]
        
        if no_cache:
            args.append("--no-cache")
        
        if build_args:
            for key, value in build_args.items():
                args.extend(["--build-arg", f"{key}={value}"])
        
        args.append(context)
        
        cmd = " ".join(shlex.quote(a) for a in args)
        code, stdout, stderr = self._docker(cmd, server)
        
        if code == 0:
            return Result.ok(f"Built {tag}")
        else:
            return Result.fail(stderr.strip() or stdout.strip())
    
    def tag(
        self,
        source: str,
        target: str,
        server: Optional[str] = None,
    ) -> Result:
        """Tag an image."""
        code, stdout, stderr = self._docker(f"tag {source} {target}", server)
        
        if code == 0:
            return Result.ok(f"Tagged {source} as {target}")
        else:
            return Result.fail(stderr.strip())
    
    def login(
        self,
        username: str,
        password: str,
        registry: str = "",
        server: Optional[str] = None,
    ) -> Result:
        """Login to Docker registry."""
        cmd = f"login -u {username} -p {password}"
        if registry:
            cmd += f" {registry}"
        
        code, stdout, stderr = self._docker(cmd, server)
        
        if code == 0:
            return Result.ok("Logged in to registry")
        else:
            return Result.fail(stderr.strip())
    
    # =========================================================================
    # Network Operations
    # =========================================================================
    
    def network_create(
        self,
        name: str,
        server: Optional[str] = None,
        driver: str = "bridge",
    ) -> Result:
        """Create Docker network."""
        code, stdout, stderr = self._docker(
            f"network create --driver {driver} {name}", 
            server
        )
        
        # Ignore "already exists" error
        if code == 0 or "already exists" in stderr:
            return Result.ok(f"Network {name} ready")
        else:
            return Result.fail(stderr.strip())
    
    def network_rm(
        self,
        name: str,
        server: Optional[str] = None,
    ) -> Result:
        """Remove Docker network."""
        code, stdout, stderr = self._docker(f"network rm {name}", server)
        
        if code == 0 or "not found" in stderr.lower():
            return Result.ok(f"Network {name} removed")
        else:
            return Result.fail(stderr.strip())
    
    # =========================================================================
    # Helpers
    # =========================================================================
    
    def _parse_ports(self, ports_str: str) -> Dict[str, int]:
        """Parse Docker ports string to dict."""
        # Format: "0.0.0.0:8080->80/tcp, 0.0.0.0:443->443/tcp"
        result = {}
        if not ports_str:
            return result
        
        for mapping in ports_str.split(", "):
            if "->" in mapping:
                try:
                    host_part, container_part = mapping.split("->")
                    host_port = int(host_part.split(":")[-1])
                    container_port = int(container_part.split("/")[0])
                    result[str(container_port)] = host_port
                except (ValueError, IndexError):
                    continue
        
        return result
