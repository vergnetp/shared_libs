"""
Centralized path resolution for deployment system.
Handles Windows/Linux path differences and local/remote distinctions.
"""

from pathlib import Path
import os
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import platform as sys_platform

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


def log(msg):
    Logger.log(msg)


class PathResolver:
    """
    Centralized path resolution for all deployment operations.
    
    Design principles:
    1. Detect target OS (Windows/Linux) once and cache
    2. Provide consistent path format based on target OS
    3. Handle localhost vs remote server transparently
    4. No hardcoded paths - all derived from project/env/service
    """
    
    # Cache for OS detection results
    _os_cache: Dict[str, str] = {}
    
    @staticmethod
    def detect_target_os(server_ip: str, user: str = "root") -> str:
        """
        Detect OS of target server (cached).
        
        Returns:
            "windows" or "linux"
        """
        # Check cache first
        cache_key = f"{server_ip}:{user}"
        if cache_key in PathResolver._os_cache:
            return PathResolver._os_cache[cache_key]
        
        if server_ip == "localhost" or server_ip is None:
            # Local detection
            system = sys_platform.system().lower()
            detected_os = "windows" if system == "windows" else "linux"
        else:
            # Remote detection
            try:
                result = CommandExecuter.run_cmd("uname -s", server_ip, user)
                uname = str(result).strip().lower()
                detected_os = "windows" if "windows" in uname else "linux"
            except Exception:
                # Try Windows command as fallback
                try:
                    CommandExecuter.run_cmd("ver", server_ip, user)
                    detected_os = "windows"
                except Exception:
                    log(f"Warning: Could not detect OS for {server_ip}, assuming Linux")
                    detected_os = "linux"
        
        # Cache result
        PathResolver._os_cache[cache_key] = detected_os
        return detected_os
    
    @staticmethod
    def get_volume_host_path(
        user: str,
        project: str,
        env: str,
        service: str,
        path_type: str,
        server_ip: str
    ) -> str:
        """
        Get host path for volume mounting based on target server OS.
        
        This is the SINGLE source of truth for all volume host paths.
        
        Args:
            user: user id (e.g. "u1")
            project: Project name
            env: Environment name
            service: Service name (or None for global paths)
            path_type: Type of path ("config", "secrets", "files", "data", "logs", "backups", "monitoring")
            server_ip: Target server IP
            
        Returns:
            Properly formatted path string for Docker volume mounting
            
        Examples:
            get_volume_host_path("u1", "myapp", "prod", "api", "config", "localhost")
            -> Windows: "C:/local/u1/myapp/prod/config/api"
            -> Linux: "/local/u1/myapp/prod/config/api"
        """
        target_os = PathResolver.detect_target_os(server_ip)
        
        if server_ip == "localhost" or server_ip is None:
            # Localhost: use local/ directory
            if target_os == "windows":
                base = Path("C:/local")
            else:
                base = Path("/local")
            
            # Build path: /local/{project}/{env}/{path_type}/{service}
            if service:
                path = base / user / project / env / path_type / service
            else:
                path = base / user / project / env / path_type
        else:
            # Remote server: always Linux-style paths
            if service:
                path = Path(f"/local/{user}/{project}/{env}/{path_type}/{service}")
            else:
                path = Path(f"/local/{user}/{project}/{env}/{path_type}")
        
        # Convert to string with forward slashes (Docker requirement)
        return str(path).replace("\\", "/")
    
    @staticmethod
    def get_git_checkout_path(user: str, project: str, env: str, service: str) -> Path:
        """Get git checkout directory path"""
        base = Path(os.getenv('LOCAL_DIR', 'C:/local'))
        return base / user / "git_checkouts" / project / env / service


    @staticmethod
    def get_volume_container_path(
        service: str,
        path_type: str
    ) -> str:
        """
        Get container path for volume mounting.
        
        Container paths are standardized regardless of host OS.
        
        Args:
            service: Service name
            path_type: Type of path
            
        Returns:
            Container path (always Linux-style)
            
        Examples:
            get_volume_container_path("api", "config")
            -> "/app/config"
            
            get_volume_container_path("postgres", "data")
            -> "/var/lib/postgresql/data"
        """
        # Standard services have special paths
        if service == "postgres":
            if path_type == "data":
                return "/var/lib/postgresql/data"
            elif path_type == "config":
                return "/etc/postgresql"
            elif path_type == "secrets":
                return "/run/secrets"
        
        elif service == "redis":
            if path_type == "data":
                return "/data"
            elif path_type == "config":
                return "/usr/local/etc/redis"
            elif path_type == "secrets":
                return "/run/secrets"
        
        elif service == "nginx":
            if path_type == "config":
                return "/etc/nginx"
            elif path_type == "logs":
                return "/var/log/nginx"
            elif path_type == "secrets":
                return "/etc/ssl/certs"
        
        # Default paths for custom services
        return f"/app/{path_type}"
    
    @staticmethod
    def get_docker_volume_name(
        user: str,
        project: str,
        env: str,
        path_type: str,
        service: Optional[str] = None
    ) -> str:
        """
        Get Docker volume name for named volumes.
        
        Args:
            user: user id (e.g. "u1")
            project: Project name
            env: Environment name
            path_type: Type of volume (only data/logs/backups/monitoring use Docker volumes)
            service: Optional service name for service-specific volumes
            
        Returns:
            Docker volume name
            
        Examples:
            get_docker_volume_name("u1", "myapp", "prod", "data", "postgres")
            -> "u1_myapp_prod_data_postgres"
            
            get_docker_volume_name("u1", "myapp", "prod", "logs")
            -> "u1_myapp_prod_logs"
        """
        if service:
            return f"{user}_{project}_{env}_{path_type}_{service}"
        else:
            return f"{user}_{project}_{env}_{path_type}"
    
    @staticmethod
    def generate_volume_mount(
        user: str,
        project: str,
        env: str,
        service: str,
        path_type: str,
        server_ip: str,
        use_docker_volumes: bool = True,
        read_only: bool = False
    ) -> str:
        """
        Generate complete volume mount string for docker run.
        
        This is the SINGLE method that should generate volume mount strings.
        
        Args:
            user: user id (e.g. "u1")
            project: Project name
            env: Environment name
            service: Service name
            path_type: Type of path
            server_ip: Target server IP
            use_docker_volumes: Use Docker named volumes for data/logs (recommended)
            read_only: Mount as read-only
            
        Returns:
            Complete volume mount string for docker run -v flag
            
        Examples:
            generate_volume_mount("u1", "myapp", "prod", "api", "config", "localhost")
            -> "C:/local/u1/myapp/prod/config/api:/app/config:ro"
            
            generate_volume_mount("u1", "myapp", "prod", "postgres", "data", use_docker_volumes=True)
            -> "u1_myapp_prod_data_postgres:/var/lib/postgresql/data"
        """
        # Push operations (config, secrets, files) always use host mounts
        # Pull operations (data, logs, backups, monitoring) can use Docker volumes
        should_use_docker_volume = (
            use_docker_volumes and 
            path_type in ("data", "logs", "backups", "monitoring")
        )
        
        if should_use_docker_volume:
            # Use Docker named volume
            volume_name = PathResolver.get_docker_volume_name(user, project, env, path_type, service)
            container_path = PathResolver.get_volume_container_path(service, path_type)
            return f"{volume_name}:{container_path}"
        else:
            # Use host mount
            host_path = PathResolver.get_volume_host_path(user, project, env, service, path_type, server_ip)
            container_path = PathResolver.get_volume_container_path(service, path_type)
            
            # Add read-only flag if needed
            ro_flag = ":ro" if read_only else ""
            
            return f"{host_path}:{container_path}{ro_flag}"
    
    @staticmethod
    def ensure_host_directories(user: str, project: str, env: str, service: str, server_ip: str): 
        host_mount_types = ["config", "secrets", "files"]
        
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {}
            
            for path_type in host_mount_types:
                host_path = PathResolver.get_volume_host_path(
                    user, project, env, service, path_type, server_ip
                )
                future = executor.submit(
                    DockerExecuter.mkdir_on_server, 
                    host_path, server_ip, user
                )
                futures[future] = host_path
            
            # Log as they complete
            for future in as_completed(futures):
                host_path = futures[future]
                try:
                    future.result()
                    log(f"Ensured directory exists: {host_path}")
                except Exception as e:
                    log(f"Warning: Could not create directory {host_path}: {e}")
    
    @staticmethod
    def ensure_docker_volumes(user: str, project: str, env: str, service: str, server_ip: str="localhost") -> None:      
        docker_volume_types = ["data", "logs", "backups", "monitoring"]
        
        def create_volume_if_needed(path_type):
            volume_name = PathResolver.get_docker_volume_name(
                user, project, env, path_type, service
            )
            
            if not DockerExecuter.volume_exists(volume_name, server_ip, user):
                DockerExecuter.create_volume(volume_name, server_ip, user)
                return (volume_name, "created")
            else:
                return (volume_name, "exists")
        
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(create_volume_if_needed, path_type): path_type
                for path_type in docker_volume_types
            }
            
            for future in as_completed(futures):
                path_type = futures[future]
                try:
                    volume_name, status = future.result()
                    if status == "created":
                        log(f"Created Docker volume: {volume_name}")
                    else:
                        log(f"Docker volume already exists: {volume_name}")
                except Exception as e:
                    log(f"Warning: Could not create Docker volume for {path_type}: {e}")
    
    @staticmethod
    def ensure_nginx_cert_directories(
        target_server: str,
        user: str
    ):
        """
        Ensure nginx certificate directories exist.
        
        Nginx uses a special directory structure that doesn't follow
        the standard project/env/service pattern.
        
        Args:
            target_server: Target server IP
            user: SSH user for remote servers
        """
        target_os = PathResolver.detect_target_os(target_server)
        
        if target_server == "localhost" or target_server is None:
            if target_os == "windows":
                base = Path("C:/local/nginx/certs")
            else:
                base = Path("/local/nginx/certs")
        else:
            base = Path("/local/nginx/certs")
        
        # Create all nginx cert subdirectories
        cert_subdirs = ["letsencrypt", "letsencrypt_var", "letsencrypt_log", "ssl"]
        
        for subdir in cert_subdirs:
            cert_path = str(base / subdir).replace("\\", "/")
            try:
                DockerExecuter.mkdir_on_server(cert_path, target_server, user)
                log(f"Ensured nginx cert directory exists: {cert_path}")
            except Exception as e:
                log(f"Warning: Could not create nginx cert directory {cert_path}: {e}")
    
    @staticmethod
    def generate_all_volume_mounts(
        user: str,
        project: str,
        env: str,
        service: str,
        server_ip: str, 
        use_docker_volumes: bool = True,        
        auto_create_dirs: bool = True
    ) -> List[str]:
        """
        Generate all volume mounts for a service on a specific target server.
        
        Automatically ensures host directories and Docker volumes exist before returning volume mounts.
        This replaces DeploymentSyncer.generate_service_volumes().
        
        IMPORTANT: server_ip is REQUIRED because volume paths depend on target OS.
        Do not default to "localhost" - caller must explicitly specify target.
        
        Args:
            user: user id (e.g. "u1")
            project: Project name
            env: Environment name
            service: Service name
            server_ip: Target server IP (REQUIRED for correct path resolution)
            use_docker_volumes: Use Docker volumes for data/logs            
            auto_create_dirs: Automatically create host directories (default: True)
            
        Returns:
            List of volume mount strings ready for docker run
            
        Example:
            # During deployment to specific blue server
            volumes = PathResolver.generate_all_volume_mounts(
                "u1", "myapp", "prod", "api", 
                server_ip="10.0.0.5"  # The specific blue server we're deploying to
            )
            # Directories automatically created on 10.0.0.5 before returning
        """
        # Step 1: Ensure host directories exist (if enabled)
        if auto_create_dirs:
            PathResolver.ensure_host_directories(
                user, project, env, service, server_ip
            )
            
            # Step 1b: Ensure Docker volumes exist (if using them)
            if use_docker_volumes:
                PathResolver.ensure_docker_volumes(
                    user, project, env, service, server_ip
                )
        
        # Step 2: Generate volume mounts
        volumes = []
        
        # Config (read-only, host mount)
        volumes.append(
            PathResolver.generate_volume_mount(
                user, project, env, service, "config", server_ip, 
                use_docker_volumes=False, read_only=True
            )
        )
        
        # Secrets (read-only, host mount)
        volumes.append(
            PathResolver.generate_volume_mount(
                user, project, env, service, "secrets", server_ip,
                use_docker_volumes=False, read_only=True
            )
        )
        
        # Files (read-only, host mount) - shared files
        volumes.append(
            PathResolver.generate_volume_mount(
                user, project, env, service, "files", server_ip,
                use_docker_volumes=False, read_only=True
            )
        )
        
        # Data (Docker volume or host mount)
        volumes.append(
            PathResolver.generate_volume_mount(
                user, project, env, service, "data", server_ip,
                use_docker_volumes=use_docker_volumes, read_only=False
            )
        )
        
        # Logs (Docker volume or host mount)
        volumes.append(
            PathResolver.generate_volume_mount(
                user, project, env, service, "logs", server_ip,
                use_docker_volumes=use_docker_volumes, read_only=False
            )
        )
        
        return volumes