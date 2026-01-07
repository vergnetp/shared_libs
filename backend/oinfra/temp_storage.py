"""
Temporary storage paths for build artifacts and debug files.

All paths in this module point to temporary directories that can be cleaned up.
For persistent configuration storage, use config_storage.py instead.

Design:
- Uses OS temp directories (automatically cleaned by OS)
- Keeps build artifacts isolated per project/service
- Optional debug logging (can be disabled)
"""

from pathlib import Path
import tempfile
from typing import Optional


class TempStorage:
    """
    Temporary storage for build artifacts and debug files.
    
    All data stored here is temporary and can be deleted safely.
    Uses system temp directory for automatic cleanup.
    """
    
    @staticmethod
    def get_temp_base() -> Path:
        """
        Get base temporary directory.
        
        Returns:
            Path to system temp directory
        """
        return Path(tempfile.gettempdir()) / 'deployment_infra'
    
    @staticmethod
    def get_dockerfiles_path(user: str) -> Path:
        """
        Get path for temporary Dockerfiles during build.
        
        Args:
            user: User ID (for isolation)
            
        Returns:
            Path to dockerfiles directory
            
        Example:
            /tmp/deployment_infra/dockerfiles/u1/
        """
        folder = TempStorage.get_temp_base() / 'dockerfiles' / user
        folder.mkdir(exist_ok=True, parents=True)
        return folder
    
    @staticmethod
    def get_dockerfile_path(user: str, project: str, env: str, service: str) -> Path:
        """
        Get path for a specific temporary Dockerfile.
        
        Args:
            user: User ID
            project: Project name
            env: Environment
            service: Service name
            
        Returns:
            Path to temporary Dockerfile
            
        Example:
            /tmp/deployment_infra/dockerfiles/u1/Dockerfile.myapp-prod-api.tmp
        """
        folder = TempStorage.get_dockerfiles_path(user)
        return folder / f"Dockerfile.{project}-{env}-{service}.tmp"
    
    @staticmethod
    def get_deployment_debug_path(deployment_id: str) -> Path:
        """
        Get path for deployment debug/audit files.
        
        OPTIONAL: Only create if debug logging is enabled.
        Set DEBUG_DEPLOYMENTS=1 in environment to enable.
        
        Args:
            deployment_id: Unique deployment ID
            
        Returns:
            Path to debug directory
            
        Example:
            /tmp/deployment_infra/deployments/deployment_12345/
        """
        folder = TempStorage.get_temp_base() / 'deployments' / deployment_id
        folder.mkdir(exist_ok=True, parents=True)
        return folder
    
    @staticmethod
    def cleanup_old_dockerfiles(user: str, keep_recent: int = 10):
        """
        Clean up old temporary Dockerfiles.
        
        Keeps only the N most recent files to prevent accumulation.
        
        Args:
            user: User ID
            keep_recent: Number of recent files to keep
        """
        dockerfiles_dir = TempStorage.get_dockerfiles_path(user)
        if not dockerfiles_dir.exists():
            return
        
        # Get all Dockerfiles sorted by modification time
        dockerfiles = sorted(
            dockerfiles_dir.glob("Dockerfile.*.tmp"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        
        # Delete old files
        for dockerfile in dockerfiles[keep_recent:]:
            try:
                dockerfile.unlink()
            except Exception:
                pass  # Ignore errors during cleanup
    
    @staticmethod
    def cleanup_old_deployments(days: int = 7):
        """
        Clean up old deployment debug directories.
        
        Args:
            days: Delete debug files older than this many days
        """
        import time
        deployments_dir = TempStorage.get_temp_base() / 'deployments'
        if not deployments_dir.exists():
            return
        
        cutoff_time = time.time() - (days * 24 * 60 * 60)
        
        for deployment_dir in deployments_dir.iterdir():
            if not deployment_dir.is_dir():
                continue
            
            # Check if directory is old
            if deployment_dir.stat().st_mtime < cutoff_time:
                try:
                    import shutil
                    shutil.rmtree(deployment_dir)
                except Exception:
                    pass  # Ignore errors during cleanup
    
    @staticmethod
    def cleanup_all(user: Optional[str] = None):
        """
        Clean up all temporary files.
        
        Args:
            user: Clean specific user's files, or None for all users
        """
        base = TempStorage.get_temp_base()
        
        if user:
            # Clean specific user's dockerfiles
            dockerfiles_dir = TempStorage.get_dockerfiles_path(user)
            if dockerfiles_dir.exists():
                import shutil
                shutil.rmtree(dockerfiles_dir, ignore_errors=True)
        else:
            # Clean everything
            if base.exists():
                import shutil
                shutil.rmtree(base, ignore_errors=True)