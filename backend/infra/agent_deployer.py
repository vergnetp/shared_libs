from pathlib import Path
from typing import Dict, List, Any, Optional
import platform
import uuid
import base64
import time
import tarfile
import io
import os
try:
    from .logger import Logger
except ImportError:
    from logger import Logger


def get_agent():
    try:
        from .health_monitor import HealthMonitor
    except ImportError:
        from health_monitor import HealthMonitor
    return HealthMonitor

def log(msg):
    Logger.log(msg)

class AgentDeployer:
    """
    High-level deployment operations using health agent.
    
    Replaces SSH-based deployment with HTTP agent calls.
    Provides container management, file operations, and image handling.
    
    MIGRATION GUIDE:
    ----------------
    Old (SSH):                          New (HTTP Agent):
    DockerExecuter.run_container()   -> AgentDeployer.run_container()
    DockerExecuter.stop_container()  -> AgentDeployer.stop_container()
    DockerExecuter.remove_container()-> AgentDeployer.remove_container()
    DockerExecuter.get_logs()        -> AgentDeployer.get_container_logs()
    CommandExecuter.run_cmd()        -> AgentDeployer methods (context-specific)
    """
    
    CHUNK_SIZE = 5 * 1024 * 1024  # 5MB chunks
    
    # =============================================================================
    # CONTAINER LIFECYCLE MANAGEMENT
    # =============================================================================
    
    @staticmethod
    def run_container(
        server_ip: str,
        name: str,
        image: str,
        ports: Optional[Dict[str, str]] = None,
        volumes: Optional[List[str]] = None,
        env_vars: Optional[Dict[str, str]] = None,
        network: Optional[str] = None,
        restart_policy: str = "unless-stopped",
        command: Optional[List[str]] = None,
        timeout: int = 60
    ) -> bool:
        """
        Start a container on remote server via agent.
        
        Replaces: DockerExecuter.run_container()
        
        Args:
            server_ip: Target server IP
            name: Container name
            image: Docker image (e.g., 'postgres:15', 'myapp:latest')
            ports: Port mappings {host_port: container_port}
            volumes: Volume mounts ['host_path:container_path', ...]
            env_vars: Environment variables {key: value}
            network: Docker network name
            restart_policy: Restart policy (unless-stopped, always, no, on-failure)
            command: Command to run (optional)
            timeout: Request timeout in seconds
            
        Returns:
            True if container started successfully
            
        Example:
            AgentDeployer.run_container(
                server_ip="10.0.0.5",
                name="myapp-api-prod",
                image="myuser/myapp:v1.0",
                ports={"8080": "80"},
                volumes=["/local/myapp/config:/app/config:ro"],
                env_vars={"DATABASE_URL": "postgres://..."},
                network="myapp-prod-network"
            )
        """
        log(f"[{server_ip}] Starting container: {name}")
        
        try:
            payload = {
                'name': name,
                'image': image,
                'ports': ports or {},
                'volumes': volumes or [],
                'env_vars': env_vars or {},
                'restart_policy': restart_policy
            }
            
            if network:
                payload['network'] = network
            
            if command:
                payload['command'] = command
            
            response = get_agent().agent_request(
                server_ip,
                "POST",
                "/containers/run",
                json_data=payload,
                timeout=timeout
            )
            
            if response.get('status') == 'started':
                container_id = response.get('container_id', 'unknown')
                log(f"[{server_ip}] ✓ Container {name} started (ID: {container_id[:12]})")
                return True
            else:
                log(f"[{server_ip}] ✗ Failed to start {name}: {response.get('error', 'unknown')}")
                return False
                
        except Exception as e:
            log(f"[{server_ip}] ✗ Error starting {name}: {e}")
            return False
    
    @staticmethod
    def stop_container(
        server_ip: str,
        name: str,
        timeout: int = 30,
        ignore_if_not_exists: bool = True
    ) -> bool:
        """
        Stop a container on remote server via agent.
        
        Replaces: DockerExecuter.stop_container()
        
        Args:
            server_ip: Target server IP
            name: Container name
            timeout: Request timeout in seconds
            ignore_if_not_exists: Don't fail if container doesn't exist
            
        Returns:
            True if container stopped successfully (or didn't exist and ignored)
        """
        log(f"[{server_ip}] Stopping container: {name}")
        
        try:
            response = get_agent().agent_request(
                server_ip,
                "POST",
                f"/containers/{name}/stop",
                timeout=timeout
            )
            
            if response.get('status') == 'stopped':
                log(f"[{server_ip}] ✓ Container {name} stopped")
                return True
            else:
                error = response.get('error', 'unknown')
                if ignore_if_not_exists and 'not found' in error.lower():
                    log(f"[{server_ip}] Container {name} not found (already stopped)")
                    return True
                log(f"[{server_ip}] ✗ Failed to stop {name}: {error}")
                return False
                
        except Exception as e:
            error_msg = str(e).lower()
            if ignore_if_not_exists and 'not found' in error_msg:
                log(f"[{server_ip}] Container {name} not found (already stopped)")
                return True
            log(f"[{server_ip}] ✗ Error stopping {name}: {e}")
            return False
    
    @staticmethod
    def remove_container(
        server_ip: str,
        name: str,
        timeout: int = 30,
        ignore_if_not_exists: bool = True
    ) -> bool:
        """
        Remove a container on remote server via agent.
        
        Replaces: DockerExecuter.remove_container()
        
        Args:
            server_ip: Target server IP
            name: Container name
            timeout: Request timeout in seconds
            ignore_if_not_exists: Don't fail if container doesn't exist
            
        Returns:
            True if container removed successfully (or didn't exist and ignored)
        """
        log(f"[{server_ip}] Removing container: {name}")
        
        try:
            response = get_agent().agent_request(
                server_ip,
                "POST",
                f"/containers/{name}/remove",
                timeout=timeout
            )
            
            if response.get('status') == 'removed':
                log(f"[{server_ip}] ✓ Container {name} removed")
                return True
            else:
                error = response.get('error', 'unknown')
                if ignore_if_not_exists and 'not found' in error.lower():
                    log(f"[{server_ip}] Container {name} not found (already removed)")
                    return True
                log(f"[{server_ip}] ✗ Failed to remove {name}: {error}")
                return False
                
        except Exception as e:
            error_msg = str(e).lower()
            if ignore_if_not_exists and 'not found' in error_msg:
                log(f"[{server_ip}] Container {name} not found (already removed)")
                return True
            log(f"[{server_ip}] ✗ Error removing {name}: {e}")
            return False
    
    @staticmethod
    def stop_and_remove_container(
        server_ip: str,
        name: str,
        timeout: int = 30,
        ignore_if_not_exists: bool = True
    ) -> bool:
        """
        Stop and remove a container in sequence.
        
        Replaces: DockerExecuter.stop_and_remove_container()
        
        Args:
            server_ip: Target server IP
            name: Container name
            timeout: Request timeout per operation
            ignore_if_not_exists: Don't fail if container doesn't exist
            
        Returns:
            True if both operations successful
        """
        log(f"[{server_ip}] Stopping and removing container: {name}")
        
        stop_success = AgentDeployer.stop_container(
            server_ip, name, timeout, ignore_if_not_exists
        )
        
        if not stop_success:
            return False
        
        # Wait a moment for container to fully stop
        time.sleep(1)
        
        remove_success = AgentDeployer.remove_container(
            server_ip, name, timeout, ignore_if_not_exists
        )
        
        return remove_success
    
    @staticmethod
    def restart_container(
        server_ip: str,
        name: str,
        timeout: int = 30
    ) -> bool:
        """
        Restart a container on remote server via agent.
        
        Args:
            server_ip: Target server IP
            name: Container name
            timeout: Request timeout in seconds
            
        Returns:
            True if container restarted successfully
        """
        log(f"[{server_ip}] Restarting container: {name}")
        
        try:
            response = get_agent().agent_request(
                server_ip,
                "POST",
                f"/containers/{name}/restart",
                timeout=timeout
            )
            
            if response.get('status') == 'restarted':
                log(f"[{server_ip}] ✓ Container {name} restarted")
                return True
            else:
                log(f"[{server_ip}] ✗ Failed to restart {name}: {response.get('error', 'unknown')}")
                return False
                
        except Exception as e:
            log(f"[{server_ip}] ✗ Error restarting {name}: {e}")
            return False
    
    # =============================================================================
    # CONTAINER QUERIES & STATUS
    # =============================================================================
    
    @staticmethod
    def list_containers(
        server_ip: str,
        timeout: int = 10
    ) -> List[Dict[str, Any]]:
        """
        List all containers on remote server via agent.
        
        Args:
            server_ip: Target server IP
            timeout: Request timeout in seconds
            
        Returns:
            List of container dicts with name, status, image
        """
        try:
            response = get_agent().agent_request(
                server_ip,
                "GET",
                "/containers",
                timeout=timeout
            )
            
            return response.get('containers', [])
            
        except Exception as e:
            log(f"[{server_ip}] Failed to list containers: {e}")
            return []
    
    @staticmethod
    def get_container_status(
        server_ip: str,
        name: str,
        timeout: int = 10
    ) -> Optional[Dict[str, Any]]:
        """
        Get status of a specific container.
        
        Replaces: DockerExecuter.get_container_status()
        
        Args:
            server_ip: Target server IP
            name: Container name
            timeout: Request timeout in seconds
            
        Returns:
            Container info dict or None if not found
        """
        try:
            response = get_agent().agent_request(
                server_ip,
                "GET",
                f"/containers/{name}",
                timeout=timeout
            )
            
            return {
                'name': response.get('name'),
                'status': response.get('status'),
                'running': response.get('running'),
                'image': response.get('image')
            }
            
        except Exception as e:
            if 'not found' in str(e).lower() or '404' in str(e):
                return None
            log(f"[{server_ip}] Failed to get status for {name}: {e}")
            return None
    
    @staticmethod
    def is_container_running(
        server_ip: str,
        name: str,
        timeout: int = 10
    ) -> bool:
        """
        Check if container is running.
        
        Args:
            server_ip: Target server IP
            name: Container name
            timeout: Request timeout in seconds
            
        Returns:
            True if container is running
        """
        status = AgentDeployer.get_container_status(server_ip, name, timeout)
        return status is not None and status.get('running', False)
    
    @staticmethod
    def get_container_logs(
        server_ip: str,
        name: str,
        lines: int = 100,
        timeout: int = 30
    ) -> str:
        """
        Get container logs from remote server via agent.
        
        Replaces: DockerExecuter.get_logs()
        
        Args:
            server_ip: Target server IP
            name: Container name
            lines: Number of lines to retrieve
            timeout: Request timeout in seconds
            
        Returns:
            Log output as string
        """
        try:
            response = get_agent().agent_request(
                server_ip,
                "GET",
                f"/containers/{name}/logs?lines={lines}",
                timeout=timeout
            )
            
            return response.get('logs', '')
            
        except Exception as e:
            log(f"[{server_ip}] Failed to get logs for {name}: {e}")
            return f"Error retrieving logs: {e}"
    
    # =============================================================================
    # IMAGE MANAGEMENT
    # =============================================================================
    
    @staticmethod
    def pull_image(
        server_ip: str,
        image: str,
        timeout: int = 600
    ) -> bool:
        """
        Pull Docker image on remote server via agent.
        
        Args:
            server_ip: Target server IP
            image: Docker image (e.g., 'postgres:15')
            timeout: Request timeout in seconds (default: 10 minutes for large images)
            
        Returns:
            True if successful
        """
        log(f"[{server_ip}] Pulling image: {image}")
        
        try:
            response = get_agent().agent_request(
                server_ip,
                "POST",
                f"/images/{image}/pull",
                timeout=timeout
            )
            
            if response.get('status') == 'pulled':
                log(f"[{server_ip}] ✓ Image {image} pulled")
                return True
            else:
                log(f"[{server_ip}] ✗ Failed to pull {image}: {response.get('error', 'unknown')}")
                return False
                
        except Exception as e:
            log(f"[{server_ip}] ✗ Error pulling {image}: {e}")
            return False
    
    # =============================================================================
    # HIGH-LEVEL DEPLOYMENT OPERATIONS
    # =============================================================================
    
    @staticmethod
    def deploy_container(
        server_ip: str,
        container_config: Dict[str, Any],
        pull_image: bool = True
    ) -> bool:
        """
        Deploy a container on remote server via agent.
        
        High-level operation that optionally pulls image first, then starts container.
        
        Args:
            server_ip: Target server IP
            container_config: Container configuration dict with:
                - name: Container name
                - image: Docker image
                - ports: Port mappings {host: container}
                - volumes: Volume mappings ['host:container']
                - env_vars: Environment variables {key: value}
                - network: Docker network name
                - restart_policy: Restart policy (default: unless-stopped)
            pull_image: Whether to pull image first (default: True)
                
        Returns:
            True if successful
        """
        container_name = container_config.get('name')
        image = container_config.get('image')
        
        log(f"[{server_ip}] Deploying container: {container_name}")
        
        try:
            # Optionally pull image first
            if pull_image:
                if not AgentDeployer.pull_image(server_ip, image):
                    log(f"[{server_ip}] Warning: Image pull failed, attempting deployment anyway")
            
            # Start container
            success = AgentDeployer.run_container(
                server_ip=server_ip,
                name=container_config['name'],
                image=container_config['image'],
                ports=container_config.get('ports'),
                volumes=container_config.get('volumes'),
                env_vars=container_config.get('env_vars'),
                network=container_config.get('network'),
                restart_policy=container_config.get('restart_policy', 'unless-stopped'),
                command=container_config.get('command')
            )
            
            return success
                
        except Exception as e:
            log(f"[{server_ip}] ✗ Failed to deploy {container_name}: {e}")
            return False
    
    @staticmethod
    def verify_container_running(
        server_ip: str,
        container_name: str,
        timeout: int = 30
    ) -> bool:
        """
        Verify container is running and healthy.
        
        Polls container status until running or timeout.
        
        Args:
            server_ip: Target server IP
            container_name: Container name to check
            timeout: How long to wait for container to be healthy
            
        Returns:
            True if container is running
        """       
        log(f"[{server_ip}] Verifying container: {container_name}")
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if AgentDeployer.is_container_running(server_ip, container_name):
                log(f"[{server_ip}] ✓ Container {container_name} is running")
                return True
            
            time.sleep(2)
        
        log(f"[{server_ip}] ✗ Container {container_name} not running after {timeout}s")
        return False
    
    # =============================================================================
    # FILE OPERATIONS
    # =============================================================================
    
    @staticmethod
    def push_files_to_server(
        server_ip: str,
        project: str,
        env: str,
        directories: List[str] = None
    ) -> bool:
        """
        Push config/secrets/files to server via agent.
        
        Args:
            server_ip: Target server IP
            project: Project name
            env: Environment name
            directories: List of dirs to push (default: ['config', 'secrets', 'files'])
            
        Returns:
            True if successful
        """
        if directories is None:
            directories = ['config', 'secrets', 'files']
        
        log(f"[{server_ip}] Pushing files for {project}/{env}...")
        
        # Get local base path        
        if platform.system() == 'Windows':
            local_base = Path(f"C:/local/{project}/{env}")
        else:
            local_base = Path(f"/local/{project}/{env}")
        
        remote_base = f"/local/{project}/{env}"
        
        # Create tar of all directories
        try:
            tar_buffer = io.BytesIO()
            
            with tarfile.open(fileobj=tar_buffer, mode='w:gz') as tar:
                for dir_name in directories:
                    dir_path = local_base / dir_name
                    
                    if not dir_path.exists():
                        log(f"[{server_ip}] Skipping {dir_name} (doesn't exist)")
                        continue
                    
                    log(f"[{server_ip}] Adding {dir_name} to archive...")
                    
                    # Add entire directory tree
                    for root, dirs, files in os.walk(dir_path):
                        for file in files:
                            file_path = os.path.join(root, file)
                            # Preserve directory structure
                            arcname = os.path.relpath(file_path, local_base)
                            tar.add(file_path, arcname=arcname)
            
            tar_data = tar_buffer.getvalue()
            tar_size_mb = len(tar_data) / 1024 / 1024
            log(f"[{server_ip}] Archive created: {tar_size_mb:.2f} MB")
            
            # Upload via chunked endpoint
            success = AgentDeployer._upload_tar_chunked(
                server_ip,
                tar_data,
                remote_base
            )
            
            if success:
                log(f"[{server_ip}] ✓ Files pushed successfully")
                return True
            else:
                log(f"[{server_ip}] ✗ Failed to push files")
                return False
                
        except Exception as e:
            log(f"[{server_ip}] Failed to create/push archive: {e}")
            return False
    
    @staticmethod
    def _upload_tar_chunked(server_ip: str, tar_data: bytes, extract_path: str) -> bool:
        """
        Upload tar file in chunks via agent.
        
        Args:
            server_ip: Target server IP
            tar_data: Tar.gz file bytes
            extract_path: Where to extract on remote server
            
        Returns:
            True if successful
        """       
        upload_id = str(uuid.uuid4())
        total_size = len(tar_data)
        total_chunks = (total_size + AgentDeployer.CHUNK_SIZE - 1) // AgentDeployer.CHUNK_SIZE
        
        log(f"[{server_ip}] Uploading {total_size / 1024 / 1024:.2f} MB in {total_chunks} chunks...")
        
        try:
            for chunk_index in range(total_chunks):
                start = chunk_index * AgentDeployer.CHUNK_SIZE
                end = min(start + AgentDeployer.CHUNK_SIZE, total_size)
                chunk_data = tar_data[start:end]
                
                # Base64 encode chunk
                chunk_b64 = base64.b64encode(chunk_data).decode('utf-8')
                
                log(f"[{server_ip}] Uploading chunk {chunk_index + 1}/{total_chunks} ({len(chunk_data) / 1024:.0f} KB)...")
                
                # Upload chunk
                response = HealthMonitor.agent_request(
                    server_ip,
                    "POST",
                    "/upload/tar/chunked",
                    json_data={
                        'upload_id': upload_id,
                        'chunk_index': chunk_index,
                        'total_chunks': total_chunks,
                        'chunk_data': chunk_b64,
                        'extract_path': extract_path
                    },
                    timeout=300  # 5 minutes per chunk
                )
                
                if chunk_index == total_chunks - 1:
                    # Last chunk - should be extracted
                    if response.get('status') == 'complete':
                        log(f"[{server_ip}] ✓ Upload complete and extracted to {extract_path}")
                        return True
                    else:
                        log(f"[{server_ip}] Unexpected final response: {response}")
                        return False
                else:
                    # Intermediate chunk
                    if response.get('status') != 'chunk_received':
                        log(f"[{server_ip}] Unexpected chunk response: {response}")
                        return False
            
            return True
            
        except Exception as e:
            log(f"[{server_ip}] Chunked upload failed: {e}")
            return False