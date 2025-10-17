from typing import Union, List, Dict, Any
from execute_cmd import CommandExecuter
from execute_docker import DockerExecuter
from pathlib import Path
from logger import Logger
from concurrent.futures import ThreadPoolExecutor, as_completed
import platform
import os

BASE = "local"

def log(msg):
    Logger.log(msg)

class DeploymentSyncer:
    """
    Universal synchronization utility with clean push/pull/sync API.
    
    SCOPE: Only handles file sync operations between local machine and remote servers.
    PATH GENERATION: Handled by PathResolver (see path_resolver.py).
    
    This class is ONLY for:
    - Pushing config/secrets/files TO servers (PARALLEL)
    - Pulling data/logs/backups FROM servers (PARALLEL)
    - Bidirectional sync operations
    
    IMPORTANT: Volume mount generation has been moved to PathResolver.
    Use PathResolver.generate_all_volume_mounts() for deployment operations.
    """

    @staticmethod
    def get_local_base(project: str, env: str) -> Path:
        """
        Get local base path for file sync operations.
        
        NOTE: This is for file sync only (push/pull operations).
        For volume mounting, use PathResolver.get_volume_host_path().
        
        Returns:
            Path to local sync directory
        """
        if platform.system() == 'Windows':
            local_base = Path("C:/") / BASE / project / env
        else:
            local_base = Path("/", BASE, project, env)
        return local_base        

    @staticmethod
    def get_remote_base(project: str, env: str) -> str:
        """
        Get remote base path for file sync operations.
        
        NOTE: This is for file sync only (push/pull operations).
        For volume mounting, use PathResolver.get_volume_host_path().
        
        Returns:
            String path on remote server
        """
        remote_base = f"/{BASE}/{project}/{env}"
        return remote_base

    @staticmethod
    def get_volume_prefix(project: str, env: str) -> str:
        """Generate Docker volume name prefix"""
        return f"{project}_{env}"
       
    @staticmethod
    def _sanitize_server_ip(server_ip: str) -> str:
        """Convert IP address to filesystem-safe folder name"""
        return server_ip.replace('.', '_')

    # =============================================================================
    # PUBLIC API - Clean push/pull/sync interface
    # =============================================================================

    @staticmethod
    def push(project: str, env: str, targets: Union[str, List[str]] = None) -> bool:
        """
        Push local content (config, secrets, files) to remote servers - OPTIMIZED & PARALLEL.
        Single archive, parallel transfer to all servers.
        
        Args:
            project: Project name
            env: Environment name  
            targets: Server IPs to push to, or None for default
            
        Returns:
            True if push completed successfully
        """
        log(f"Pushing content for {project}/{env}")
        Logger.start()
        
        # Get paths
        local_base = DeploymentSyncer.get_local_base(project, env)
        remote_base = DeploymentSyncer.get_remote_base(project, env)
        
        # Ensure local directories exist
        push_dirs = ['config', 'secrets', 'files']
        for dir_name in push_dirs:
            dir_path = local_base / dir_name
            dir_path.mkdir(parents=True, exist_ok=True)
        
        # Get target servers and filter localhost
        target_servers = DeploymentSyncer._resolve_targets(targets, ['localhost'])
        remote_servers = [s for s in target_servers if s != 'localhost']
        
        if not remote_servers:
            log("No remote servers to push to")
            Logger.end()
            return True
        
        # Check if there's anything to push
        has_content = False
        content_summary = []
        for dir_name in push_dirs:
            dir_path = local_base / dir_name
            if dir_path.exists():
                file_count = sum(1 for _ in dir_path.rglob('*') if _.is_file())
                if file_count > 0:
                    has_content = True
                    content_summary.append(f"{dir_name}: {file_count} files")
        
        if not has_content:
            log("No content to push (all directories empty)")
            Logger.end()
            return True
        
        log(f"Content to push: {', '.join(content_summary)}")
        
        # Create single tar archive of all push directories
        import tarfile
        import io
        
        log("Creating archive...")
        tar_buffer = io.BytesIO()
        
        with tarfile.open(fileobj=tar_buffer, mode='w:gz') as tar:
            for dir_name in push_dirs:
                dir_path = local_base / dir_name
                if dir_path.exists():
                    # Add entire directory tree preserving structure
                    for root, dirs, files in os.walk(dir_path):
                        for file in files:
                            file_path = os.path.join(root, file)
                            # Archive path relative to local_base preserves structure
                            arcname = os.path.relpath(file_path, local_base).replace('\\', '/')
                            tar.add(file_path, arcname=arcname)
        
        tar_data = tar_buffer.getvalue()
        archive_size_mb = len(tar_data) / 1024 / 1024
        log(f"Archive created: {archive_size_mb:.2f} MB")
        
        # ========== OPTIMIZATION: Push to all servers in PARALLEL ==========
        log(f"Pushing to {len(remote_servers)} servers: {remote_servers}")
        
        def push_to_server(server_ip):
            """Push archive to a single server"""
            try:
                log(f"Pushing to {server_ip}...")
                
                # Ensure remote directory exists
                CommandExecuter.run_cmd(f"mkdir -p {remote_base}", server_ip, "root")
                
                # Transfer and extract in one command
                extract_cmd = f"cd {remote_base} && tar -xzf -"
                CommandExecuter.run_cmd_with_stdin(extract_cmd, tar_data, server_ip, "root")
                
                # Set permissions on secrets directory if it exists
                CommandExecuter.run_cmd(
                    f"if [ -d {remote_base}/secrets ]; then "
                    f"find {remote_base}/secrets -type d -exec chmod 700 {{}} \\; && "
                    f"find {remote_base}/secrets -type f -exec chmod 600 {{}} \\;; "
                    f"fi",
                    server_ip, "root"
                )
                
                log(f"  ✓ Successfully pushed to {server_ip}")
                return (server_ip, True, None)
                
            except Exception as e:
                log(f"  ✗ Failed to push to {server_ip}: {e}")
                return (server_ip, False, str(e))
        
        # Push to all servers in parallel
        success = True
        with ThreadPoolExecutor(max_workers=min(len(remote_servers), 5)) as executor:
            futures = [executor.submit(push_to_server, ip) for ip in remote_servers]
            
            for future in as_completed(futures):
                server_ip, server_success, error = future.result()
                if not server_success:
                    success = False
        
        Logger.end()
        log(f"Push {'complete' if success else 'completed with errors'}")
        return success

    @staticmethod  
    def pull(project: str, env: str, targets: Union[str, List[str]] = None) -> bool:
        """
        Pull generated content (data, logs, backups, monitoring) from remote servers/containers - PARALLEL.
        
        Args:
            project: Project name
            env: Environment name
            targets: Server IPs to pull from, or None for default
            
        Returns:
            True if pull completed successfully
        """
        log(f"Pulling content for {project}/{env}")
        Logger.start()
        
        # ========== OPTIMIZATION: Pull all types in PARALLEL ==========
        def pull_sync_type(sync_type):
            """Pull a single sync type"""
            try:
                DeploymentSyncer._sync_type(project, env, sync_type, targets, direction='pull')
                return (sync_type, True, None)
            except Exception as e:
                return (sync_type, False, str(e))
        
        success = True
        pull_types = ['data', 'logs', 'backups', 'monitoring']
        
        # Pull all types in parallel
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(pull_sync_type, t) for t in pull_types]
            
            for future in as_completed(futures):
                sync_type, type_success, error = future.result()
                if type_success:
                    log(f"  ✓ Pulled {sync_type}")
                else:
                    log(f"  ✗ Failed to pull {sync_type}: {error}")
                    success = False
        
        Logger.end()
        log("Pull complete" if success else "Pull completed with errors")
        return success

    @staticmethod
    def sync(project: str, env: str, targets: Union[str, List[str]] = None) -> bool:
        """
        Full bidirectional sync - push local content and pull generated content.
        
        Args:
            project: Project name
            env: Environment name
            targets: Server IPs to sync with, or None for default
            
        Returns:
            True if sync completed successfully
        """
        log(f"Full sync for {project}/{env}")
        Logger.start()
        
        push_success = DeploymentSyncer.push(project, env, targets)
        pull_success = DeploymentSyncer.pull(project, env, targets)
        
        success = push_success and pull_success
        
        Logger.end()
        log("Sync complete" if success else "Sync completed with errors")
        return success

    # =============================================================================
    # INTERNAL IMPLEMENTATION
    # =============================================================================

    @staticmethod
    def _sync_type(project: str, env: str, sync_type: str, targets: Union[str, List[str]], direction: str):
        """Internal method to sync a specific type in a specific direction"""
        
        # For push operations of config/secrets/files, this should not be called anymore
        # as the optimized push() method handles all three at once
        if direction == 'push' and sync_type in ['config', 'secrets', 'files']:
            log(f"Warning: _sync_type called for {sync_type} push - should use push() method directly")
            return
        
        sync_configs = DeploymentSyncer._get_sync_configs(project, env)
        
        if sync_type not in sync_configs:
            raise ValueError(f"Invalid sync type: {sync_type}")
        
        config = sync_configs[sync_type]
        
        # Validate direction matches config
        is_push_operation = (direction == 'push')
        is_push_config = config.get('push', False)
        
        if is_push_operation != is_push_config:
            raise ValueError(f"Cannot {direction} {sync_type} - it's configured for {'push' if is_push_config else 'pull'}")
        
        # Handle based on storage type - determine by presence of volume_name
        if 'volume_name' in config:
            DeploymentSyncer._sync_docker_volume(project, env, sync_type, config, direction)
        else:
            # This path should only be used for pull operations now
            DeploymentSyncer._sync_host_mount(project, env, sync_type, config, targets, direction)

    @staticmethod
    def _sync_docker_volume(project: str, env: str, sync_type: str, config: Dict[str, Any], direction: str):
        """Handle Docker volume sync operations"""
        
        if direction == 'push':
            # Push to Docker volume (not typically needed, volumes are for generated content)
            log(f"Warning: Pushing to Docker volume {sync_type} - this is unusual")
            return
            
        # Pull from Docker volume - handle both global and service-specific volumes
        volume_name = config['volume_name']
        local_path = Path(config['local_path'])
        
        # Create local directory
        local_path.mkdir(parents=True, exist_ok=True)
        
        # ========== OPTIMIZATION: Check if volume exists and has content ==========
        try:
            check_cmd = f'docker volume inspect {volume_name}'
            CommandExecuter.run_cmd(check_cmd)
                
        except Exception:
            log(f"Skipping {sync_type} - volume {volume_name} does not exist")
            return
        
        # Handle both global volumes and service-specific volumes
        if '_' in volume_name and len(volume_name.split('_')) >= 3:
            # Service-specific volumes like "project_env_logs_service"
            DeploymentSyncer._sync_service_volumes(project, env, local_path)
        else:
            # Global volumes like "project_env_logs"  
            if DockerExecuter.volume_exists(volume_name):
                DeploymentSyncer._copy_from_docker_volume(volume_name, local_path)
            else:
                log(f"Volume {volume_name} does not exist, skipping")

    @staticmethod
    def _sync_service_volumes(project: str, env: str, base_local_path: Path):
        """Sync all service-specific volumes for a type (e.g., all service logs) - PARALLEL"""
        volume_prefix = DeploymentSyncer.get_volume_prefix(project, env)
        
        # Find all volumes matching our pattern
        all_volumes = DeploymentSyncer.list_volumes()
        
        # Extract volume type from base_local_path (e.g., "logs" from "C:/local/project/env/logs")
        volume_type = base_local_path.name
        
        # Find service-specific volumes for this type
        service_volumes = [v for v in all_volumes if v.startswith(f"{volume_prefix}_{volume_type}_")]
        
        if not service_volumes:
            return
        
        # ========== OPTIMIZATION: Pull service volumes in PARALLEL ==========
        def pull_service_volume(volume_name):
            """Pull volume for a single service"""
            try:
                # Extract service name from volume name
                # e.g., "project_env_logs_worker" -> "worker"
                parts = volume_name.split('_')
                if len(parts) >= 4:
                    service_name = '_'.join(parts[3:])  # Handle service names with underscores
                    
                    # Create service directory directly under the type directory
                    service_local_path = base_local_path / service_name
                    service_local_path.mkdir(parents=True, exist_ok=True)
                    
                    # Copy directly from volume to service directory
                    DeploymentSyncer._copy_from_docker_volume(volume_name, service_local_path)
                    return (service_name, True)
            except Exception as e:
                log(f"Failed to pull {volume_name}: {e}")
                return (volume_name, False)
            return (volume_name, False)
        
        with ThreadPoolExecutor(max_workers=min(len(service_volumes), 4)) as executor:
            futures = [executor.submit(pull_service_volume, vol) for vol in service_volumes]
            for future in as_completed(futures):
                service_name, pulled = future.result()
                if pulled:
                    log(f"Pulled {volume_type} for {service_name}")

    @staticmethod
    def _copy_from_docker_volume(volume_name: str, local_path: Path):
        """Copy files from Docker volume to local directory"""
        local_path_str = str(local_path).replace('\\', '/')
        cmd = f'docker run --rm -v {volume_name}:/source -v "{local_path_str}":/dest alpine sh -c "cp -r /source/* /dest/ 2>/dev/null || true"'
        
        CommandExecuter.run_cmd(cmd)

    @staticmethod
    def list_volumes() -> List[str]:
        """List all Docker volumes"""
        try:
            result = CommandExecuter.run_cmd(["docker", "volume", "ls", "--format", "{{.Name}}"])
            if hasattr(result, 'stdout'):
                return [name.strip() for name in result.stdout.split('\n') if name.strip()]
            else:
                return [name.strip() for name in str(result).split('\n') if name.strip()]
        except:
            return []

    @staticmethod
    def _sync_host_mount(project: str, env: str, sync_type: str, config: Dict[str, Any], 
                        targets: Union[str, List[str]], direction: str):
        """
        Handle host mount sync operations - ONLY for pull operations now.
        Push operations should use the optimized push() method.
        """
        
        if direction == 'push':
            log(f"Warning: _sync_host_mount should not be used for push - use push() method")
            return
        
        # Pull operations remain unchanged
        target_servers = DeploymentSyncer._resolve_targets(targets, ['localhost'])
        local_dir = config['local_path']
        Path(local_dir).mkdir(parents=True, exist_ok=True)
        
        # Pull: Remote servers → Local (with server separation)
        for server_ip in target_servers:
            if server_ip == 'localhost':
                continue
            
            remote_path = config['remote_path'].rstrip('/')
            sanitized_ip = DeploymentSyncer._sanitize_server_ip(server_ip)
            server_local_dir = Path(local_dir) / sanitized_ip
            server_local_dir.mkdir(parents=True, exist_ok=True)
            
            try:
                # Check if remote directory exists first
                CommandExecuter.run_cmd(f"test -d {remote_path}", server_ip, "root")
                
                # Create tar on remote and transfer back
                tar_cmd = f"cd {remote_path} && tar -czf - ."
                result = CommandExecuter.run_cmd(tar_cmd, server_ip, "root")
                
                # Extract locally
                import tarfile
                import io
                
                # Handle the result properly - it should be bytes or string
                if isinstance(result, str):
                    tar_data = result.encode('latin-1')
                elif isinstance(result, bytes):
                    tar_data = result
                else:
                    # Handle subprocess.CompletedProcess or other types
                    if hasattr(result, 'stdout'):
                        if isinstance(result.stdout, bytes):
                            tar_data = result.stdout
                        else:
                            tar_data = result.stdout.encode('latin-1')
                    else:
                        tar_data = str(result).encode('latin-1')
                
                # Extract tar to local directory
                with tarfile.open(fileobj=io.BytesIO(tar_data), mode='r:gz') as tar:
                    tar.extractall(path=server_local_dir)
                
                log(f"Successfully pulled {sync_type} from {server_ip}")
            except Exception as e:
                log(f"Remote path {remote_path} may not exist on {server_ip}: {e}")

    @staticmethod
    def _get_sync_configs(project: str, env: str) -> Dict[str, Dict[str, Any]]:
        """Get sync configuration with hybrid Docker volume approach"""
        
        local_base = DeploymentSyncer.get_local_base(project, env)
        remote_base = DeploymentSyncer.get_remote_base(project, env)
        local_base_str = str(local_base).replace('\\', '/')
        volume_prefix = DeploymentSyncer.get_volume_prefix(project, env)
        
        return {
            # Push operations (host mounts for easy editing)
            'config': {
                'local_path': f"{local_base_str}/config",  
                'remote_path': f"{remote_base}/config",   
                'secure_perms': False,
                'push': True
            },
            'secrets': {
                'local_path': f"{local_base_str}/secrets",  
                'remote_path': f"{remote_base}/secrets",
                'secure_perms': True,
                'push': True
            },
            'files': {
                'local_path': f"{local_base_str}/files",  
                'remote_path': f"{remote_base}/files",
                'secure_perms': False,
                'push': True
            },
            
            # Pull operations (Docker volumes for performance)
            'data': {
                'local_path': f"{local_base_str}/data",
                'volume_name': f"{volume_prefix}_data",
                'push': False
            },
            'logs': {
                'local_path': f"{local_base_str}/logs", 
                'volume_name': f"{volume_prefix}_logs",
                'push': False
            },
            'backups': {
                'local_path': f"{local_base_str}/backups",
                'volume_name': f"{volume_prefix}_backups", 
                'push': False
            },
            'monitoring': {
                'local_path': f"{local_base_str}/monitoring",
                'volume_name': f"{volume_prefix}_monitoring",
                'push': False
            }
        }

    @staticmethod
    def _resolve_targets(targets: Union[str, List[str]], default: List[str]) -> List[str]:
        """Resolve target specification to server list"""
        if targets is None:
            return default
        elif isinstance(targets, str):
            return [targets]
        else:
            return targets