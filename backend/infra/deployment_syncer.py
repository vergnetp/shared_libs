from typing import Union, List, Dict, Any, Optional
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import platform
import os
import shutil
import tarfile
import io

try:
    from .execute_cmd import CommandExecuter
except ImportError:
    from execute_cmd import CommandExecuter
try:
    from .execute_docker import DockerExecuter
except ImportError:
    from execute_docker import DockerExecuter
try:
    from .logger import Logger
except ImportError:
    from logger import Logger
try:
    from .backup_manager import BackupManager
except ImportError:
    from backup_manager import BackupManager
try:
    from .deployment_config import DeploymentConfigurer
except ImportError:
    from deployment_config import DeploymentConfigurer


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
    def get_local_base(user: str, project: str, env: str) -> Path:
        """
        Get local base path for file sync operations.
        
        NOTE: This is for file sync only (push/pull operations).
        For volume mounting, use PathResolver.get_volume_host_path().
        
        Args:
            user: User for resource segregation (mandatory)
            project: Project name
            env: Environment name
        
        Returns:
            Path to local sync directory
        """
        if platform.system() == 'Windows':
            local_base = Path("C:/") / BASE / user / project / env
        else:
            local_base = Path("/", BASE, user, project, env)
        return local_base        

    @staticmethod
    def get_remote_base(user: str, project: str, env: str) -> str:
        """
        Get remote base path for file sync operations.
        
        NOTE: This is for file sync only (push/pull operations).
        For volume mounting, use PathResolver.get_volume_host_path().
        
        Args:
            user: User for resource segregation (mandatory)
            project: Project name
            env: Environment name
        
        Returns:
            String path on remote server
        """
        remote_base = f"/{BASE}/{user}/{project}/{env}"
        return remote_base

    @staticmethod
    def get_volume_prefix(user: str, project: str, env: str) -> str:
        """
        Generate Docker volume name prefix.
        
        Args:
            user: User for resource segregation (mandatory)
            project: Project name
            env: Environment name
        
        Returns:
            Volume prefix string (e.g., "user_project_env")
        """
        return f"{user}_{project}_{env}"
       
    @staticmethod
    def _sanitize_server_ip(server_ip: str) -> str:
        """Convert IP address to filesystem-safe folder name"""
        return server_ip.replace('.', '_')

    @staticmethod
    def _copy_stateful_secrets_to_consumers(user: str, project: str, env: str, services: Dict[str, Dict[str, Any]]):
        """
        Copy secrets from stateful services (postgres, redis, mongo) to all consumer services.
        
        This allows services to read database passwords from their own /app/secrets directory
        without mounting the entire secrets folder, improving security isolation.
        
        Logic:
        1. Use BackupManager.detect_service_type() to identify stateful services
        2. Find all secret files in stateful service directories
        3. Copy each secret file to all non-stateful (consumer) service directories
        4. Preserves file metadata and overwrites existing files
        
        Example:
            Before:
                secrets/
                ├── postgres/
                │   └── db_password
                ├── redis/
                │   └── redis_password
                └── api/
                    └── jwt_secret
            
            After (before push):
                secrets/
                ├── postgres/
                │   └── db_password
                ├── redis/
                │   └── redis_password
                └── api/
                    ├── db_password      ← Copied from postgres
                    ├── redis_password   ← Copied from redis
                    └── jwt_secret       ← Original
        
        Args:
            project: Project name
            env: Environment name
            services: Dictionary of all services in the environment
        """        
        local_base = DeploymentSyncer.get_local_base(user, project, env)
        secrets_base = local_base / "secrets"
        
        if not secrets_base.exists():
            log("No secrets directory found - skipping stateful secrets distribution")
            return
        
        # Find all stateful services using BackupManager
        stateful_services = []
        for service_name, service_config in services.items():
            service_type = BackupManager.detect_service_type(service_name, service_config)
            if service_type:  # postgres, redis, mongo, mysql, etc.
                stateful_services.append(service_name)
        
        if not stateful_services:
            log("No stateful services found - skipping secrets distribution")
            return
        
        log(f"Distributing secrets from stateful services: {stateful_services}")
        
        # Find all consumer services (non-stateful)
        consumer_services = [s for s in services.keys() if s not in stateful_services]
        
        if not consumer_services:
            log("No consumer services found - skipping secrets distribution")
            return
        
        # Copy each stateful service's secrets to all consumers
        copied_count = 0
        for stateful_service in stateful_services:
            stateful_secrets_dir = secrets_base / stateful_service
            
            if not stateful_secrets_dir.exists():
                log(f"  No secrets directory for {stateful_service} - skipping")
                continue
            
            # Get all secret files from this stateful service
            secret_files = [f for f in stateful_secrets_dir.iterdir() if f.is_file()]
            
            if not secret_files:
                log(f"  No secret files in {stateful_service} - skipping")
                continue
            
            # Copy to all consumer services
            for secret_file in secret_files:
                for consumer_service in consumer_services:
                    consumer_secrets_dir = secrets_base / consumer_service
                    consumer_secrets_dir.mkdir(parents=True, exist_ok=True)
                    
                    dest_file = consumer_secrets_dir / secret_file.name
                    
                    # Copy (overwrite if exists)
                    shutil.copy2(secret_file, dest_file)
                    copied_count += 1
                    
                    log(f"  ✓ {stateful_service}/{secret_file.name} → {consumer_service}/{secret_file.name}")
        
        if copied_count > 0:
            log(f"Distributed {copied_count} secret file(s) to {len(consumer_services)} consumer service(s)")

    # =============================================================================
    # PUBLIC API - Clean push/pull/sync interface
    # =============================================================================

    @staticmethod
    def push(user: str, project: str, env: str, targets: Optional[Union[str, List[str]]] = None) -> bool:
        """
        Push local content (config, secrets, files) to remote servers - OPTIMIZED & PARALLEL.
        Single archive, parallel transfer to all servers.
        
        Args:
            user: User for resource segregation (mandatory)
            project: Project name
            env: Environment name  
            targets: Server IPs to push to, or None for default
            
        Returns:
            True if push completed successfully
        """
        log(f"Pushing content for {user}/{project}/{env}")
        Logger.start()
        
        # Get paths
        local_base = DeploymentSyncer.get_local_base(user, project, env)
        remote_base = DeploymentSyncer.get_remote_base(user, project, env)
        
        # Ensure local directories exist
        push_dirs = ['config', 'secrets', 'files']
        for dir_name in push_dirs:
            dir_path = local_base / dir_name
            dir_path.mkdir(parents=True, exist_ok=True)

        try:            
            configurer = DeploymentConfigurer(user, project)
            services = configurer.get_services(env)
            DeploymentSyncer._copy_stateful_secrets_to_consumers(user, project, env, services)
        except Exception as e:
            log(f"Warning: Could not distribute stateful secrets: {e}")
        
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
    def pull(user: str, project: str, env: str, targets: Optional[Union[str, List[str]]] = None) -> bool:
        """
        Pull generated content (data, logs, backups, monitoring) from remote servers/containers - PARALLEL.
        
        Args:
            user: User for resource segregation (mandatory)
            project: Project name
            env: Environment name
            targets: Server IPs to pull from, or None for default
            
        Returns:
            True if pull completed successfully
        """
        log(f"Pulling content for {user}/{project}/{env}")
        Logger.start()
        
        # ========== OPTIMIZATION: Pull all types in PARALLEL ==========
        def pull_sync_type(sync_type):
            """Pull a single sync type"""
            try:
                DeploymentSyncer._sync_type(user, project, env, sync_type, targets, direction='pull')
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
    def sync(user: str, project: str, env: str, targets: Optional[Union[str, List[str]]] = None) -> bool:
        """
        Full bidirectional sync - push local content and pull generated content.
        
        Args:
            project: Project name
            env: Environment name
            user: Optional user for resource segregation
            targets: Server IPs to sync with, or None for default
            
        Returns:
            True if sync completed successfully
        """
        log(f"Full sync for {user}/{project}/{env}")
        Logger.start()
        
        push_success = DeploymentSyncer.push(user, project, env, targets)
        pull_success = DeploymentSyncer.pull(user, project, env, targets)
        
        success = push_success and pull_success
        
        Logger.end()
        log("Sync complete" if success else "Sync completed with errors")
        return success

    # =============================================================================
    # INTERNAL IMPLEMENTATION
    # =============================================================================

    @staticmethod
    def _sync_type(user: str, project: str, env: str, sync_type: str, targets: Union[str, List[str]], direction: str):
        """Internal method to sync a specific type in a specific direction"""
        
        # For push operations of config/secrets/files, this should not be called anymore
        # as the optimized push() method handles all three at once
        if direction == 'push' and sync_type in ['config', 'secrets', 'files']:
            log(f"Warning: _sync_type called for {sync_type} push - should use push() method directly")
            return
        
        sync_configs = DeploymentSyncer._get_sync_configs(user, project, env)
        
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
            DeploymentSyncer._sync_docker_volume(user, project, env, sync_type, config, direction)
        else:
            # This path should only be used for pull operations now
            DeploymentSyncer._sync_host_mount(user, project, env, sync_type, config, targets, direction)

    @staticmethod
    def _sync_docker_volume(user: str, project: str, env: str, sync_type: str, config: Dict[str, Any], direction: str):
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
        # Volume format: user_project_env_logs (global) or user_project_env_logs_service (service-specific)
        if '_' in volume_name and len(volume_name.split('_')) > 4:
            # Service-specific volumes like "user_project_env_logs_service"
            DeploymentSyncer._sync_service_volumes(user, project, env, local_path)
        else:
            # Global volumes like "user_project_env_logs"
            if DockerExecuter.volume_exists(volume_name):
                DeploymentSyncer._copy_from_docker_volume(volume_name, local_path)
            else:
                log(f"Volume {volume_name} does not exist, skipping")

    @staticmethod
    def _sync_service_volumes(user: str, project: str, env: str, base_local_path: Path):
        """Sync all service-specific volumes for a type (e.g., all service logs) - PARALLEL"""
        volume_prefix = DeploymentSyncer.get_volume_prefix(user, project, env)
        
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
                # Format: user_project_env_logs_service
                # e.g., "alice_myapp_prod_logs_worker" -> "worker"
                parts = volume_name.split('_')
                if len(parts) >= 5:
                    service_name = '_'.join(parts[4:])  # Handle service names with underscores
                    
                    if service_name:
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
    def _sync_host_mount(user: str, project: str, env: str, sync_type: str, config: Dict[str, Any], targets: Union[str, List[str]], direction: str):
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
    def _get_sync_configs(user: str, project: str, env: str) -> Dict[str, Dict[str, Any]]:
        """Get sync configuration with hybrid Docker volume approach"""
        
        local_base = DeploymentSyncer.get_local_base(user, project, env)
        remote_base = DeploymentSyncer.get_remote_base(user, project, env)
        local_base_str = str(local_base).replace('\\', '/')
        volume_prefix = DeploymentSyncer.get_volume_prefix(user, project, env)
        
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
        
    @staticmethod
    def push_directory(local_dir: Path, remote_base_path: str, server_ips: List[str], 
                      set_permissions: bool = False, dir_perms: str = "700", 
                      file_perms: str = "600", parallel: bool = True) -> bool:
        """
        Push a local directory to multiple remote servers using tar streaming.
        
        Low-level utility for pushing arbitrary directories. For standard 
        config/secrets/files operations, use push() instead.
        
        Args:
            local_dir: Local directory to push (e.g., Path("/local/myapp/prod/secrets"))
            remote_base_path: Remote base path where contents will be extracted (e.g., "/local/myapp/prod")
            server_ips: List of server IPs to push to
            set_permissions: If True, set permissions after extraction
            dir_perms: Permissions for directories (e.g., "700")
            file_perms: Permissions for files (e.g., "600")
            parallel: If True, push to servers in parallel (default: True)
            
        Returns:
            True if push completed successfully to all servers
            
        Example:
            # Push secrets directory to servers with secure permissions
            DeploymentSyncer.push_directory(
                local_dir=Path("/local/myapp/prod/secrets"),
                remote_base_path="/local/myapp/prod",
                server_ips=["192.168.1.100", "192.168.1.101"],
                set_permissions=True,
                dir_perms="700",
                file_perms="600"
            )
        """        
        if not local_dir.exists():
            log(f"Warning: Local directory not found: {local_dir}")
            return False
        
        try:
            # Create tar archive
            log(f"Creating archive of {local_dir}...")
            tar_buffer = io.BytesIO()
            
            with tarfile.open(fileobj=tar_buffer, mode='w:gz') as tar:
                for root, dirs, files in os.walk(local_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        # Archive path relative to parent of local_dir to preserve directory structure
                        arcname = os.path.relpath(file_path, local_dir.parent).replace('\\', '/')
                        tar.add(file_path, arcname=arcname)
            
            tar_data = tar_buffer.getvalue()
            archive_size_mb = len(tar_data) / 1024 / 1024
            log(f"Archive created: {archive_size_mb:.2f} MB")
            
            # Push to servers (parallel or sequential)
            if parallel:
                return DeploymentSyncer._push_directory_parallel(
                    tar_data, remote_base_path, server_ips, 
                    set_permissions, dir_perms, file_perms, local_dir.name
                )
            else:
                return DeploymentSyncer._push_directory_sequential(
                    tar_data, remote_base_path, server_ips,
                    set_permissions, dir_perms, file_perms, local_dir.name
                )
            
        except Exception as e:
            log(f"Error creating archive: {e}")
            return False
    
    @staticmethod
    def _push_directory_parallel(tar_data: bytes, remote_base_path: str, server_ips: List[str],
                                set_permissions: bool, dir_perms: str, file_perms: str, dir_name: str) -> bool:
        """Push directory to servers in parallel"""
        def push_to_server(server_ip):
            """Push archive to a single server"""
            try:
                log(f"Pushing to {server_ip}...")
                
                # Ensure remote directory exists
                CommandExecuter.run_cmd(f"mkdir -p {remote_base_path}", server_ip, "root")
                
                # Transfer and extract tar archive
                extract_cmd = f"cd {remote_base_path} && tar -xzf -"
                CommandExecuter.run_cmd_with_stdin(extract_cmd, tar_data, server_ip, "root")
                
                # Set permissions if requested
                if set_permissions:
                    CommandExecuter.run_cmd(
                        f"find {remote_base_path}/{dir_name} -type d -exec chmod {dir_perms} {{}} \\; && "
                        f"find {remote_base_path}/{dir_name} -type f -exec chmod {file_perms} {{}} \\;",
                        server_ip, "root"
                    )
                
                log(f"✓ Successfully pushed to {server_ip}")
                return (server_ip, True, None)
                
            except Exception as e:
                log(f"❌ Failed to push to {server_ip}: {e}")
                return (server_ip, False, str(e))
        
        # Push to all servers in parallel
        success = True
        with ThreadPoolExecutor(max_workers=min(len(server_ips), 5)) as executor:
            futures = [executor.submit(push_to_server, ip) for ip in server_ips]
            
            for future in as_completed(futures):
                server_ip, server_success, error = future.result()
                if not server_success:
                    success = False
        
        return success
    
    @staticmethod
    def _push_directory_sequential(tar_data: bytes, remote_base_path: str, server_ips: List[str],
                                   set_permissions: bool, dir_perms: str, file_perms: str, dir_name: str) -> bool:
        """Push directory to servers sequentially"""
        success = True
        
        for server_ip in server_ips:
            try:
                log(f"Pushing to {server_ip}...")
                
                # Ensure remote directory exists
                CommandExecuter.run_cmd(f"mkdir -p {remote_base_path}", server_ip, "root")
                
                # Transfer and extract tar archive
                extract_cmd = f"cd {remote_base_path} && tar -xzf -"
                CommandExecuter.run_cmd_with_stdin(extract_cmd, tar_data, server_ip, "root")
                
                # Set permissions if requested
                if set_permissions:
                    CommandExecuter.run_cmd(
                        f"find {remote_base_path}/{dir_name} -type d -exec chmod {dir_perms} {{}} \\; && "
                        f"find {remote_base_path}/{dir_name} -type f -exec chmod {file_perms} {{}} \\;",
                        server_ip, "root"
                    )
                
                log(f"✓ Successfully pushed to {server_ip}")
                
            except Exception as e:
                log(f"❌ Failed to push to {server_ip}: {e}")
                success = False
        
        return success