"""
Local Deployer - Deploy services to localhost.

MVP implementation for testing. Builds Docker image and runs container locally.
"""

from __future__ import annotations
import os
import subprocess
import shlex
import tempfile
import shutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Callable

from .generator import DockerfileGenerator, DockerfileConfig


@dataclass
class DeployConfig:
    """Configuration for a deployment."""
    service_name: str
    source_path: str  # Local folder path
    port: int = 8000
    env_vars: Dict[str, str] = field(default_factory=dict)
    
    # For app_kernel services with shared_libs
    shared_libs_path: Optional[str] = None
    
    # Docker options
    network: Optional[str] = None
    volumes: List[str] = field(default_factory=list)
    restart_policy: str = "unless-stopped"
    
    # Build options
    no_cache: bool = False
    
    @property
    def image_name(self) -> str:
        return f"local/{self.service_name}:latest"
    
    @property
    def container_name(self) -> str:
        return f"{self.service_name}"


@dataclass 
class DeployResult:
    """Result of a deployment."""
    success: bool
    container_id: Optional[str] = None
    container_name: Optional[str] = None
    port: Optional[int] = None
    message: str = ""
    logs: List[str] = field(default_factory=list)


class LocalDeployer:
    """
    Deploy services to localhost using Docker.
    
    Usage:
        deployer = LocalDeployer()
        
        # Deploy from local folder
        result = deployer.deploy(DeployConfig(
            service_name="ai-agents",
            source_path="/path/to/services/ai_agents",
            port=8001,
        ))
        
        # Deploy app_kernel service with shared_libs
        result = deployer.deploy(DeployConfig(
            service_name="ai-agents",
            source_path="/path/to/services/ai_agents",
            shared_libs_path="/path/to/shared_libs",
            port=8001,
        ))
        
        # Stop service
        deployer.stop("ai-agents")
    """
    
    def __init__(self, log_callback: Optional[Callable[[str], None]] = None):
        self._temp_dirs: List[str] = []
        self._log_callback = log_callback
    
    def _make_log_list(self) -> List[str]:
        """Create a log list that also calls the callback."""
        callback = self._log_callback
        
        class LogList(list):
            def append(self, item):
                super().append(item)
                if callback:
                    try:
                        callback(item)
                    except:
                        pass
        
        return LogList()
    
    def __del__(self):
        self.cleanup()
    
    def cleanup(self):
        """Clean up temporary directories."""
        for d in self._temp_dirs:
            try:
                shutil.rmtree(d)
            except Exception:
                pass
        self._temp_dirs = []
    
    # =========================================================================
    # Main Deploy Method
    # =========================================================================
    
    def deploy(self, config: DeployConfig) -> DeployResult:
        """
        Deploy a service locally.
        
        Steps:
        1. Generate Dockerfile if needed
        2. Prepare build context
        3. Build image
        4. Stop existing container (if any)
        5. Run new container
        """
        logs = self._make_log_list()
        
        try:
            # Check Docker is available
            if not self._check_docker():
                return DeployResult(
                    success=False,
                    message="Docker is not available. Please ensure Docker is installed and running.",
                    logs=logs,
                )
            
            logs.append(f"Starting deployment of {config.service_name}")
            
            # Prepare build context
            logs.append("Preparing build context...")
            try:
                build_context, dockerfile_path = self._prepare_build_context(config)
            except Exception as e:
                import traceback
                logs.append(f"Context prep error: {e}")
                logs.append(traceback.format_exc())
                return DeployResult(
                    success=False,
                    message=f"Failed to prepare build context: {e}",
                    logs=logs,
                )
            
            logs.append(f"Build context: {build_context}")
            logs.append(f"Dockerfile: {dockerfile_path}")
            
            # Show generated Dockerfile
            try:
                with open(dockerfile_path, 'r') as f:
                    dockerfile_content = f.read()
                logs.append("--- Dockerfile ---")
                for line in dockerfile_content.split('\n')[:20]:  # First 20 lines
                    logs.append(line)
                logs.append("--- End Dockerfile ---")
            except Exception as e:
                logs.append(f"Could not read Dockerfile: {e}")
            
            # Build image
            logs.append(f"Building image {config.image_name}...")
            
            def log_build_line(line):
                logs.append(line)
            
            try:
                build_success, build_output = self._build_image(
                    dockerfile_path=dockerfile_path,
                    context=build_context,
                    tag=config.image_name,
                    no_cache=config.no_cache,
                    log_callback=log_build_line,
                )
            except Exception as e:
                import traceback
                logs.append(f"Build exception: {e}")
                logs.append(traceback.format_exc())
                return DeployResult(
                    success=False,
                    message=f"Build exception: {e}",
                    logs=logs,
                )
            
            if not build_success:
                # Get last 20 lines of build output
                output_lines = build_output.split('\n') if build_output else []
                last_lines = output_lines[-20:] if len(output_lines) > 20 else output_lines
                return DeployResult(
                    success=False,
                    message=f"Failed to build image",
                    logs=logs + last_lines,
                )
            
            logs.append("Image built successfully")
            
            # Stop existing container
            logs.append(f"Stopping existing container {config.container_name}...")
            self.stop(config.container_name)
            
            # Run new container
            logs.append(f"Starting container on port {config.port}...")
            container_id = self._run_container(config)
            
            if not container_id:
                return DeployResult(
                    success=False,
                    message="Failed to start container",
                    logs=logs,
                )
            
            container_short = container_id[:12] if container_id and len(container_id) >= 12 else container_id
            logs.append(f"Container started: {container_short}")
            
            # Poll container health - check multiple times to detect crash loops
            import time
            logs.append("Waiting for container to initialize...")
            
            # Wait and check multiple times to catch crash loops
            is_healthy = False
            final_status = None
            
            for check_num in range(4):  # Check 4 times over 8 seconds
                time.sleep(2)
                status = self.status(config.container_name)
                
                if not status:
                    continue
                    
                final_status = status
                is_running = status.get("running", False)
                container_status = status.get("status", "unknown")
                restart_count = status.get("restart_count", 0)
                exit_code = status.get("exit_code", 0)
                
                # Crash loop detected: restarting status OR restart count > 0
                if container_status == "restarting" or restart_count > 0:
                    logs.append(f"Container status: {container_status} (restarts: {restart_count})")
                    break
                
                # Container exited/dead
                if container_status in ("exited", "dead"):
                    logs.append(f"Container status: {container_status} (exit code: {exit_code})")
                    break
                
                # Running - but check logs for errors (fast crashes might look "running")
                if is_running and container_status == "running":
                    # On last check, verify no errors in logs
                    if check_num >= 2:
                        container_logs = self.logs(config.container_name, tail=30)
                        if container_logs and ("Traceback" in container_logs or "Error" in container_logs or "ModuleNotFoundError" in container_logs):
                            # Has errors - likely crash looping
                            logs.append(f"Container status: {container_status} (errors detected in logs)")
                            break
                        else:
                            is_healthy = True
                            logs.append(f"Container status: {container_status}")
                            break
            
            # Final check - is it actually healthy?
            if final_status:
                is_running = final_status.get("running", False)
                container_status = final_status.get("status", "unknown")
                restart_count = final_status.get("restart_count", 0)
                exit_code = final_status.get("exit_code", 0)
                
                # Also check logs for errors even if "running"
                container_logs = self.logs(config.container_name, tail=50)
                has_errors = container_logs and (
                    "Traceback" in container_logs or 
                    "ModuleNotFoundError" in container_logs or
                    "ImportError" in container_logs
                )
                
                # Failure conditions:
                # - Not running
                # - Status is restarting/exited/dead
                # - Has restart count > 0 (crash loop)
                # - Has Python errors in logs
                is_failed = (
                    not is_running or 
                    container_status in ("restarting", "exited", "dead") or
                    restart_count > 0 or
                    has_errors
                )
                
                if is_failed:
                    # Container crashed - get logs to show why
                    logs.append("")
                    if has_errors:
                        logs.append("⚠️ Container has errors! Fetching logs...")
                    else:
                        logs.append("⚠️ Container failed! Fetching logs...")
                    logs.append("--- Container Logs ---")
                    # Re-fetch to ensure we have latest
                    container_logs = self.logs(config.container_name, tail=50)
                    if container_logs:
                        for line in container_logs.split('\n'):
                            logs.append(line)
                    else:
                        logs.append("(no logs available)")
                    logs.append("--- End Container Logs ---")
                    
                    reason = "errors in logs" if has_errors else f"status: {container_status}, restarts: {restart_count}"
                    return DeployResult(
                        success=False,
                        container_id=container_id,
                        container_name=config.container_name,
                        port=config.port,
                        message=f"Container failed ({reason}). Check logs above.",
                        logs=logs,
                    )
            
            return DeployResult(
                success=True,
                container_id=container_id,
                container_name=config.container_name,
                port=config.port,
                message=f"Service deployed at http://localhost:{config.port}",
                logs=logs,
            )
            
        except Exception as e:
            import traceback
            logs.append(f"Error: {e}")
            logs.append(traceback.format_exc())
            return DeployResult(
                success=False,
                message=str(e),
                logs=logs,
            )
    
    # =========================================================================
    # Build Context Preparation
    # =========================================================================
    
    def _prepare_build_context(self, config: DeployConfig) -> tuple[str, str]:
        """
        Prepare build context directory.
        
        For app_kernel services, we need to copy both the service
        and shared_libs into a temp directory for proper build context.
        
        Returns:
            (build_context_path, dockerfile_path)
        """
        source = Path(config.source_path).resolve()
        
        if config.shared_libs_path:
            # App kernel service - need to create combined build context
            return self._prepare_app_kernel_context(config)
        else:
            # Simple service - use source directly
            return self._prepare_simple_context(config)
    
    def _prepare_simple_context(self, config: DeployConfig) -> tuple[str, str]:
        """Prepare context for simple service (no shared_libs)."""
        source = Path(config.source_path).resolve()
        
        # Check for existing Dockerfile
        dockerfile = source / "Dockerfile"
        if dockerfile.exists():
            return str(source), str(dockerfile)
        
        # Generate Dockerfile
        temp_dir = tempfile.mkdtemp(prefix="deploy_")
        self._temp_dirs.append(temp_dir)
        
        # Copy source
        dest = Path(temp_dir) / source.name
        shutil.copytree(source, dest, dirs_exist_ok=True)
        
        # Generate and write Dockerfile
        dockerfile_content = DockerfileGenerator.generate(
            str(source),
            DockerfileConfig(port=config.port, env_vars=config.env_vars),
        )
        dockerfile_path = Path(temp_dir) / "Dockerfile"
        dockerfile_path.write_text(dockerfile_content)
        
        return temp_dir, str(dockerfile_path)
    
    def _prepare_app_kernel_context(self, config: DeployConfig) -> tuple[str, str]:
        """Prepare context for app_kernel service with shared_libs."""
        source = Path(config.source_path).resolve()
        shared_libs = Path(config.shared_libs_path).resolve()
        service_name = source.name
        
        # Create temp directory
        temp_dir = tempfile.mkdtemp(prefix="deploy_")
        self._temp_dirs.append(temp_dir)
        
        # Create structure that matches imports:
        # /app/
        # ├── shared_libs/
        # │   └── backend/
        # └── services/
        #     └── {service_name}/
        #
        # This allows: from shared_libs.backend.xxx import ...
        
        # Copy shared_libs/backend -> shared_libs/backend
        backend_src = shared_libs / "backend"
        if backend_src.exists():
            dest_backend = Path(temp_dir) / "shared_libs" / "backend"
            dest_backend.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(backend_src, dest_backend, dirs_exist_ok=True)
            
            # Create __init__.py files
            (Path(temp_dir) / "shared_libs" / "__init__.py").touch()
            (dest_backend / "__init__.py").touch()
        
        # Copy service -> services/{service_name}
        dest_service = Path(temp_dir) / "services" / service_name
        dest_service.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, dest_service, dirs_exist_ok=True)
        
        # Create __init__.py for services
        (Path(temp_dir) / "services" / "__init__.py").touch()
        
        # Generate Dockerfile
        dockerfile_content = DockerfileGenerator.generate_for_app_kernel_service(
            service_path=str(source),
            shared_libs_path=str(shared_libs),
            port=config.port,
            env_vars=config.env_vars,
        )
        dockerfile_path = Path(temp_dir) / "Dockerfile"
        dockerfile_path.write_text(dockerfile_content)
        
        return temp_dir, str(dockerfile_path)
    
    # =========================================================================
    # Docker Operations
    # =========================================================================
    
    def _check_docker(self) -> bool:
        """Check if Docker is available."""
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def _build_image(
        self,
        dockerfile_path: str,
        context: str,
        tag: str,
        no_cache: bool = False,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> tuple[bool, str]:
        """Build Docker image with streaming output."""
        cmd = ["docker", "build", "-f", dockerfile_path, "-t", tag, "--progress=plain"]
        
        if no_cache:
            cmd.append("--no-cache")
        
        cmd.append(context)
        
        output_lines = []
        process = None
        
        try:
            # Use Popen for streaming output
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding='utf-8',
                errors='replace',
            )
            
            # Read output line by line
            if process.stdout:
                for line in process.stdout:
                    line = line.rstrip()
                    output_lines.append(line)
                    if log_callback:
                        try:
                            log_callback(line)
                        except:
                            pass
            
            process.wait(timeout=600)
            
            output = "\n".join(output_lines)
            return process.returncode == 0, output
            
        except subprocess.TimeoutExpired:
            if process:
                process.kill()
            return False, "Build timed out after 10 minutes"
        except FileNotFoundError:
            return False, "Docker not found. Is Docker installed and in PATH?"
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            return False, f"Build error: {type(e).__name__}: {str(e)}\n{tb}"
    
    def _run_container(self, config: DeployConfig) -> Optional[str]:
        """Run Docker container."""
        cmd = [
            "docker", "run", "-d",
            "--name", config.container_name,
            "-p", f"{config.port}:{config.port}",
            "--restart", config.restart_policy,
        ]
        
        # Network
        if config.network:
            cmd.extend(["--network", config.network])
        
        # Volumes
        for vol in config.volumes:
            cmd.extend(["-v", vol])
        
        # Environment variables
        for key, value in config.env_vars.items():
            cmd.extend(["-e", f"{key}={value}"])
        
        # Image
        cmd.append(config.image_name)
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )
            
            if result.returncode == 0:
                return result.stdout.strip()
            else:
                print(f"Run failed: {result.stderr}")
                return None
                
        except Exception as e:
            print(f"Run error: {e}")
            return None
    
    # =========================================================================
    # Container Management
    # =========================================================================
    
    def stop(self, container_name: str) -> bool:
        """Stop and remove a container."""
        try:
            # Stop
            subprocess.run(
                ["docker", "stop", container_name],
                capture_output=True,
                timeout=30,
            )
            # Remove
            subprocess.run(
                ["docker", "rm", container_name],
                capture_output=True,
                timeout=10,
            )
            return True
        except Exception:
            return False
    
    def status(self, container_name: str) -> Optional[Dict[str, Any]]:
        """Get container status."""
        try:
            result = subprocess.run(
                ["docker", "inspect", container_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            
            if result.returncode == 0:
                import json
                data = json.loads(result.stdout)
                if data:
                    container = data[0]
                    state = container.get("State", {})
                    # RestartCount is at container level, not in State
                    return {
                        "id": container.get("Id", "")[:12],
                        "name": container_name,
                        "status": state.get("Status", "unknown"),
                        "running": state.get("Running", False),
                        "restart_count": container.get("RestartCount", 0),
                        "exit_code": state.get("ExitCode", 0),
                    }
            return None
            
        except Exception:
            return None
    
    def logs(self, container_name: str, tail: int = 100) -> str:
        """Get container logs."""
        try:
            result = subprocess.run(
                ["docker", "logs", "--tail", str(tail), container_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout + result.stderr
        except Exception as e:
            return f"Error getting logs: {e}"
