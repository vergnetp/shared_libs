"""
Backup Manager - Handles automatic backup configuration for stateful services.

This module provides:
- Service type detection (postgres, redis, etc.)
- Backup service configuration generation
- Dockerfile generation for backup containers
"""

import hashlib
from typing import Dict, Any, Optional
from deployment_naming import DeploymentNaming
from path_resolver import PathResolver


# Service types that support automatic backups
BACKUP_ENABLED_SERVICES = {
    "postgres": {
        "script": "backup_postgres.py",
        "base_image": "alpine:latest",
        "packages": ["python3", "postgresql-client"],
        "default_schedule": "0 2 * * *",  # 2 AM daily
        "default_retention": 7
    },
    "redis": {
        "script": "backup_redis.py",
        "base_image": "alpine:latest",
        "packages": ["python3", "redis"],
        "default_schedule": "0 3 * * *",  # 3 AM daily
        "default_retention": 7
    }
}


class BackupManager:
    """Manages automatic backup configuration for stateful services"""
    
    @staticmethod
    def detect_service_type(service_name: str, service_config: Dict[str, Any]) -> Optional[str]:
        """
        Detect service type from name or image.
        
        Args:
            service_name: Name of the service
            service_config: Service configuration dict
            
        Returns:
            Service type (postgres, redis, etc.) or None
        """
        # Check explicit type
        if "type" in service_config:
            service_type = service_config["type"].lower()
            if service_type in BACKUP_ENABLED_SERVICES:
                return service_type
        
        # Check image
        image = service_config.get("image", "").lower()
        for svc_type in BACKUP_ENABLED_SERVICES:
            if svc_type in image:
                return svc_type
        
        # Check name
        name_lower = service_name.lower()
        for svc_type in BACKUP_ENABLED_SERVICES:
            if svc_type in name_lower:
                return svc_type
        
        return None
    
    @staticmethod
    def is_backup_enabled(service_config: Dict[str, Any]) -> bool:
        """
        Check if backup is enabled for this service.
        Default: True (backups enabled by default)
        
        Args:
            service_config: Service configuration dict
            
        Returns:
            True if backup should be enabled
        """
        backup_config = service_config.get("backup", {})
        return backup_config.get("enabled", True)  # Default: enabled
    
    @staticmethod
    def generate_backup_service_config(
        project: str,
        env: str,
        service_name: str,
        service_config: Dict[str, Any],
        server_ip: str
    ) -> Optional[Dict[str, Any]]:
        """
        Auto-generate backup service configuration from parent service.
        
        Args:
            project: Project name
            env: Environment name
            service_name: Parent service name (e.g., "postgres")
            service_config: Parent service configuration
            server_ip: Server IP (for path resolution)
            
        Returns:
            Backup service configuration dict or None if not applicable
        """
        # Detect service type
        service_type = BackupManager.detect_service_type(service_name, service_config)
        if not service_type or service_type not in BACKUP_ENABLED_SERVICES:
            return None
        
        # Check if backup is enabled
        if not BackupManager.is_backup_enabled(service_config):
            return None
        
        backup_info = BACKUP_ENABLED_SERVICES[service_type]
        backup_config = service_config.get("backup", {})
        
        # Get parent container name for Docker DNS
        parent_container_name = DeploymentNaming.get_container_name(project, env, service_name)
        
        # Copy parent's env vars (includes POSTGRES_DB, POSTGRES_USER, etc.)
        parent_env_vars = service_config.get("env_vars", {}).copy()
        
        # Add backup-specific env vars
        backup_env_vars = {
            **parent_env_vars,
            "SERVICE_TYPE": service_type,
            "SERVICE_NAME": service_name,
            "RETENTION_DAYS": str(backup_config.get("retention_days", backup_info["default_retention"])),
            "PROJECT": project,
            "ENV": env
        }
        
        # Add host override for Docker DNS
        backup_env_vars["HOST"] = parent_container_name        
        
        # Generate backup service config
        return {
            "dockerfile_content": BackupManager.generate_backup_dockerfile(service_type),
            "build_context": ".",
            "schedule": backup_config.get("schedule", backup_info["default_schedule"]),
            "env_vars": backup_env_vars,
            "volumes": {
                # Data volume (read-only)
                PathResolver.get_docker_volume_name(project, env, "data", service_name): "/data:ro",
                # Secrets (read-only host mount)
                PathResolver.get_volume_host_path(project, env, service_name, "secrets", server_ip): "/run/secrets:ro",
                # Backups volume (write)
                PathResolver.get_docker_volume_name(project, env, "backups", service_name): "/backups"
            },
            "network": DeploymentNaming.get_network_name(project, env)
            # No server_zone, servers_count, server_cpu, server_memory
            # Backup deploys to parent's exact servers
        }
    
    @staticmethod
    def generate_backup_dockerfile(service_type: str) -> Dict[str, str]:
        """
        Generate Dockerfile content for backup service.
        
        Args:
            service_type: Type of service (postgres, redis, etc.)
            
        Returns:
            Dict with line numbers as keys and Dockerfile commands as values
        """
        if service_type not in BACKUP_ENABLED_SERVICES:
            raise ValueError(f"Unknown service type: {service_type}")
        
        backup_info = BACKUP_ENABLED_SERVICES[service_type]
        base_image = backup_info["base_image"]
        packages = backup_info["packages"]
        script = backup_info["script"]
        
        # Build package install command
        packages_str = " ".join(packages)
        
        return {
            "1": f"FROM {base_image}",
            "2": f"RUN apk add --no-cache {packages_str}",
            "3": "WORKDIR /app",
            "4": f"COPY scripts/{script} .",
            "5": f"CMD [\"python3\", \"{script}\"]"
        }