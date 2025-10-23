from pathlib import Path
from typing import Dict, List, Any
import platform
import uuid
import base64
import time
import tarfile
import io
import os
from logger import Logger
from health_monitor import HealthMonitor

def log(msg):
    Logger.log(msg)

class AgentDeployer:
    """
    High-level deployment operations using health agent.
    
    Replaces SSH-based deployment with HTTP agent calls.
    """
    
    CHUNK_SIZE = 5 * 1024 * 1024  # 5MB chunks
    
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
        
        log(f"Pushing files to {server_ip} for {project}/{env}...")
        
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
                        log(f"Skipping {dir_name} (doesn't exist)")
                        continue
                    
                    log(f"Adding {dir_name} to archive...")
                    
                    # Add entire directory tree
                    for root, dirs, files in os.walk(dir_path):
                        for file in files:
                            file_path = os.path.join(root, file)
                            # Preserve directory structure
                            arcname = os.path.relpath(file_path, local_base)
                            tar.add(file_path, arcname=arcname)
            
            tar_data = tar_buffer.getvalue()
            tar_size_mb = len(tar_data) / 1024 / 1024
            log(f"Archive created: {tar_size_mb:.2f} MB")
            
            # Upload via chunked endpoint
            success = AgentDeployer._upload_tar_chunked(
                server_ip,
                tar_data,
                remote_base
            )
            
            if success:
                log(f"✓ Files pushed successfully to {server_ip}")
                return True
            else:
                log(f"❌ Failed to push files to {server_ip}")
                return False
                
        except Exception as e:
            log(f"Failed to create/push archive: {e}")
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
        
        log(f"Uploading {total_size / 1024 / 1024:.2f} MB in {total_chunks} chunks...")
        
        try:
            for chunk_index in range(total_chunks):
                start = chunk_index * AgentDeployer.CHUNK_SIZE
                end = min(start + AgentDeployer.CHUNK_SIZE, total_size)
                chunk_data = tar_data[start:end]
                
                # Base64 encode chunk
                chunk_b64 = base64.b64encode(chunk_data).decode('utf-8')
                
                log(f"Uploading chunk {chunk_index + 1}/{total_chunks} ({len(chunk_data) / 1024:.0f} KB)...")
                
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
                        log(f"✓ Upload complete and extracted to {extract_path}")
                        return True
                    else:
                        log(f"Unexpected final response: {response}")
                        return False
                else:
                    # Intermediate chunk
                    if response.get('status') != 'chunk_received':
                        log(f"Unexpected chunk response: {response}")
                        return False
            
            return True
            
        except Exception as e:
            log(f"Chunked upload failed: {e}")
            return False
    
    @staticmethod
    def pull_image(server_ip: str, image: str) -> bool:
        """
        Pull Docker image on remote server via agent.
        
        Args:
            server_ip: Target server IP
            image: Docker image (e.g., 'postgres:15')
            
        Returns:
            True if successful
        """
        log(f"Pulling image {image} on {server_ip}...")
        
        try:
            response = HealthMonitor.agent_request(
                server_ip,
                "POST",
                f"/images/{image}/pull",
                timeout=600  # 10 minutes for large images
            )
            
            if response.get('status') == 'pulled':
                log(f"✓ Image {image} pulled successfully")
                return True
            else:
                log(f"Unexpected response: {response}")
                return False
                
        except Exception as e:
            log(f"Failed to pull image: {e}")
            return False
    
    @staticmethod
    def deploy_container(
        server_ip: str,
        container_config: Dict[str, Any]
    ) -> bool:
        """
        Deploy a container on remote server via agent.
        
        Args:
            server_ip: Target server IP
            container_config: Container configuration dict with:
                - name: Container name
                - image: Docker image
                - ports: Port mappings {host: container}
                - volumes: Volume mappings {host: container}
                - env_vars: Environment variables {key: value}
                - network: Docker network name
                - restart_policy: Restart policy (default: unless-stopped)
                
        Returns:
            True if successful
        """
        container_name = container_config.get('name')
        log(f"Deploying container {container_name} on {server_ip}...")
        
        try:
            # First, pull the image
            image = container_config['image']
            if not AgentDeployer.pull_image(server_ip, image):
                return False
            
            # Deploy container
            response = HealthMonitor.agent_request(
                server_ip,
                "POST",
                "/containers/run",
                json_data=container_config,
                timeout=60
            )
            
            if response.get('status') == 'started':
                container_id = response.get('container_id', 'unknown')
                log(f"✓ Container {container_name} started (ID: {container_id[:12]})")
                return True
            else:
                log(f"Unexpected response: {response}")
                return False
                
        except Exception as e:
            log(f"Failed to deploy container: {e}")
            return False
    
    @staticmethod
    def verify_container_running(server_ip: str, container_name: str, timeout: int = 30) -> bool:
        """
        Verify container is running and healthy.
        
        Args:
            server_ip: Target server IP
            container_name: Container name to check
            timeout: How long to wait for container to be healthy
            
        Returns:
            True if container is running
        """       
        
        log(f"Verifying container {container_name} on {server_ip}...")
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                response = HealthMonitor.agent_request(
                    server_ip,
                    "GET",
                    f"/containers/{container_name}",
                    timeout=5
                )
                
                status = response.get('status', 'unknown')
                
                if status == 'running':
                    log(f"✓ Container {container_name} is running")
                    return True
                else:
                    log(f"Container {container_name} status: {status}")
                    time.sleep(2)
                    
            except Exception as e:
                log(f"Could not check container status: {e}")
                time.sleep(2)
        
        log(f"Container {container_name} did not become healthy within {timeout}s")
        return False