"""
Backup Manager - Database and volume backups.

Handles:
- PostgreSQL/MySQL database backups
- Docker volume backups
- Backup to local/S3/remote
- Backup rotation
"""

from __future__ import annotations
import os
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path
from enum import Enum

if TYPE_CHECKING:
    from ..context import DeploymentContext
    from ..ssh.client import SSHClient

from ..core.result import Result


class BackupType(Enum):
    """Backup types."""
    DATABASE = "database"
    VOLUME = "volume"
    FILES = "files"


class StorageType(Enum):
    """Backup storage types."""
    LOCAL = "local"
    S3 = "s3"
    REMOTE = "remote"  # Via SSH


@dataclass
class BackupConfig:
    """Backup configuration."""
    name: str
    type: BackupType
    
    # Source
    source: str  # DB name, volume name, or path
    
    # Storage
    storage_type: StorageType = StorageType.LOCAL
    storage_path: str = "/backups"
    
    # S3 config (if storage_type == S3)
    s3_bucket: Optional[str] = None
    s3_prefix: Optional[str] = None
    
    # Remote config (if storage_type == REMOTE)
    remote_host: Optional[str] = None
    remote_path: Optional[str] = None
    
    # Rotation
    keep_count: int = 7  # Number of backups to keep
    keep_days: int = 30  # Or keep backups for this many days
    
    # Compression
    compress: bool = True
    
    # Database-specific
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: Optional[str] = None
    db_password: Optional[str] = None
    db_type: str = "postgres"  # postgres, mysql


@dataclass
class BackupResult:
    """Backup operation result."""
    success: bool
    backup_path: Optional[str] = None
    size_bytes: Optional[int] = None
    duration_seconds: Optional[float] = None
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "backup_path": self.backup_path,
            "size_bytes": self.size_bytes,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
            "timestamp": self.timestamp.isoformat(),
        }


class BackupManager:
    """
    Backup manager.
    
    Usage:
        backup = BackupManager(ctx)
        
        # Backup PostgreSQL database
        result = backup.backup_database(
            name="mydb",
            db_name="myapp",
            db_user="postgres",
        )
        
        # Backup Docker volume
        result = backup.backup_volume("myapp_data")
        
        # Restore
        backup.restore_database("mydb", "/backups/mydb_2024.sql.gz")
        
        # Rotate old backups
        backup.rotate_backups("mydb", keep_count=7)
    """
    
    def __init__(
        self, 
        ctx: 'DeploymentContext',
        ssh: Optional['SSHClient'] = None,
        backup_dir: str = "/backups",
    ):
        self.ctx = ctx
        self.ssh = ssh
        self.backup_dir = backup_dir
    
    def _exec(
        self, 
        cmd: str, 
        server: Optional[str] = None,
        timeout: int = 3600,
    ) -> tuple[int, str, str]:
        """Execute command."""
        if server and self.ssh:
            return self.ssh.exec(server, cmd, timeout=timeout)
        else:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
    
    def _get_backup_path(self, name: str, extension: str = "sql.gz") -> str:
        """Generate backup file path with timestamp."""
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        return f"{self.backup_dir}/{self.ctx.namespace}_{name}_{timestamp}.{extension}"
    
    def _ensure_backup_dir(self, server: Optional[str] = None):
        """Ensure backup directory exists."""
        self._exec(f"mkdir -p {self.backup_dir}", server)
    
    # =========================================================================
    # Database Backups
    # =========================================================================
    
    def backup_database(
        self,
        name: str,
        db_name: str,
        db_type: str = "postgres",
        db_host: str = "localhost",
        db_port: int = 5432,
        db_user: Optional[str] = None,
        db_password: Optional[str] = None,
        compress: bool = True,
        server: Optional[str] = None,
    ) -> BackupResult:
        """
        Backup database.
        
        Args:
            name: Backup name
            db_name: Database name
            db_type: "postgres" or "mysql"
            db_host: Database host
            db_port: Database port
            db_user: Database user
            db_password: Database password
            compress: Compress backup
            server: Remote server
            
        Returns:
            BackupResult
        """
        start = datetime.utcnow()
        self._ensure_backup_dir(server)
        
        extension = "sql.gz" if compress else "sql"
        backup_path = self._get_backup_path(name, extension)
        
        # Build dump command based on db type
        if db_type == "postgres":
            cmd = self._postgres_dump_cmd(
                db_name, db_host, db_port, db_user, db_password, backup_path, compress
            )
        elif db_type == "mysql":
            cmd = self._mysql_dump_cmd(
                db_name, db_host, db_port, db_user, db_password, backup_path, compress
            )
        else:
            return BackupResult(success=False, error=f"Unknown db_type: {db_type}")
        
        self.ctx.log_info(f"Backing up database {db_name}", backup_path=backup_path)
        
        code, stdout, stderr = self._exec(cmd, server)
        duration = (datetime.utcnow() - start).total_seconds()
        
        if code == 0:
            # Get file size
            size_code, size_out, _ = self._exec(f"stat -c%s {backup_path}", server)
            size = int(size_out.strip()) if size_code == 0 else None
            
            self.ctx.log_info(f"Database backup complete", size_bytes=size, duration=duration)
            
            return BackupResult(
                success=True,
                backup_path=backup_path,
                size_bytes=size,
                duration_seconds=duration,
            )
        else:
            self.ctx.log_error(f"Database backup failed: {stderr}")
            return BackupResult(
                success=False,
                error=stderr.strip(),
                duration_seconds=duration,
            )
    
    def _postgres_dump_cmd(
        self,
        db_name: str,
        host: str,
        port: int,
        user: Optional[str],
        password: Optional[str],
        output_path: str,
        compress: bool,
    ) -> str:
        """Build pg_dump command."""
        cmd = ["pg_dump"]
        
        cmd.extend(["-h", host, "-p", str(port)])
        
        if user:
            cmd.extend(["-U", user])
        
        cmd.append(db_name)
        
        if compress:
            cmd_str = " ".join(cmd) + f" | gzip > {output_path}"
        else:
            cmd_str = " ".join(cmd) + f" > {output_path}"
        
        if password:
            cmd_str = f"PGPASSWORD={password} {cmd_str}"
        
        return cmd_str
    
    def _mysql_dump_cmd(
        self,
        db_name: str,
        host: str,
        port: int,
        user: Optional[str],
        password: Optional[str],
        output_path: str,
        compress: bool,
    ) -> str:
        """Build mysqldump command."""
        cmd = ["mysqldump"]
        
        cmd.extend(["-h", host, "-P", str(port)])
        
        if user:
            cmd.extend(["-u", user])
        
        if password:
            cmd.append(f"-p{password}")
        
        cmd.append(db_name)
        
        if compress:
            return " ".join(cmd) + f" | gzip > {output_path}"
        else:
            return " ".join(cmd) + f" > {output_path}"
    
    def restore_database(
        self,
        backup_path: str,
        db_name: str,
        db_type: str = "postgres",
        db_host: str = "localhost",
        db_port: int = 5432,
        db_user: Optional[str] = None,
        db_password: Optional[str] = None,
        server: Optional[str] = None,
    ) -> Result:
        """
        Restore database from backup.
        
        Args:
            backup_path: Path to backup file
            db_name: Target database name
            db_type: "postgres" or "mysql"
            
        Returns:
            Result
        """
        self.ctx.log_info(f"Restoring database {db_name}", backup_path=backup_path)
        
        # Check if compressed
        is_compressed = backup_path.endswith(".gz")
        
        if db_type == "postgres":
            restore_cmd = f"psql -h {db_host} -p {db_port}"
            if db_user:
                restore_cmd += f" -U {db_user}"
            restore_cmd += f" {db_name}"
            
            if db_password:
                restore_cmd = f"PGPASSWORD={db_password} {restore_cmd}"
        
        elif db_type == "mysql":
            restore_cmd = f"mysql -h {db_host} -P {db_port}"
            if db_user:
                restore_cmd += f" -u {db_user}"
            if db_password:
                restore_cmd += f" -p{db_password}"
            restore_cmd += f" {db_name}"
        
        else:
            return Result.fail(f"Unknown db_type: {db_type}")
        
        if is_compressed:
            cmd = f"gunzip -c {backup_path} | {restore_cmd}"
        else:
            cmd = f"{restore_cmd} < {backup_path}"
        
        code, stdout, stderr = self._exec(cmd, server)
        
        if code == 0:
            self.ctx.log_info(f"Database restored: {db_name}")
            return Result.ok(f"Database {db_name} restored from {backup_path}")
        else:
            return Result.fail(stderr.strip())
    
    # =========================================================================
    # Volume Backups
    # =========================================================================
    
    def backup_volume(
        self,
        volume_name: str,
        compress: bool = True,
        server: Optional[str] = None,
    ) -> BackupResult:
        """
        Backup Docker volume.
        
        Args:
            volume_name: Docker volume name
            compress: Compress backup
            server: Remote server
            
        Returns:
            BackupResult
        """
        start = datetime.utcnow()
        self._ensure_backup_dir(server)
        
        extension = "tar.gz" if compress else "tar"
        backup_path = self._get_backup_path(volume_name, extension)
        
        self.ctx.log_info(f"Backing up volume {volume_name}", backup_path=backup_path)
        
        # Use alpine container to tar the volume
        tar_flags = "czf" if compress else "cf"
        cmd = (
            f"docker run --rm -v {volume_name}:/data -v {self.backup_dir}:/backup "
            f"alpine tar {tar_flags} /backup/{os.path.basename(backup_path)} -C /data ."
        )
        
        code, stdout, stderr = self._exec(cmd, server)
        duration = (datetime.utcnow() - start).total_seconds()
        
        if code == 0:
            # Get file size
            size_code, size_out, _ = self._exec(f"stat -c%s {backup_path}", server)
            size = int(size_out.strip()) if size_code == 0 else None
            
            return BackupResult(
                success=True,
                backup_path=backup_path,
                size_bytes=size,
                duration_seconds=duration,
            )
        else:
            return BackupResult(
                success=False,
                error=stderr.strip(),
                duration_seconds=duration,
            )
    
    def restore_volume(
        self,
        backup_path: str,
        volume_name: str,
        server: Optional[str] = None,
    ) -> Result:
        """
        Restore Docker volume from backup.
        
        Args:
            backup_path: Path to backup file
            volume_name: Target volume name
            
        Returns:
            Result
        """
        self.ctx.log_info(f"Restoring volume {volume_name}", backup_path=backup_path)
        
        is_compressed = backup_path.endswith(".gz")
        tar_flags = "xzf" if is_compressed else "xf"
        
        cmd = (
            f"docker run --rm -v {volume_name}:/data -v {self.backup_dir}:/backup "
            f"alpine tar {tar_flags} /backup/{os.path.basename(backup_path)} -C /data"
        )
        
        code, stdout, stderr = self._exec(cmd, server)
        
        if code == 0:
            return Result.ok(f"Volume {volume_name} restored")
        else:
            return Result.fail(stderr.strip())
    
    # =========================================================================
    # Backup Rotation
    # =========================================================================
    
    def rotate_backups(
        self,
        name_pattern: str,
        keep_count: Optional[int] = None,
        keep_days: Optional[int] = None,
        server: Optional[str] = None,
    ) -> Result:
        """
        Rotate old backups.
        
        Args:
            name_pattern: Pattern to match backup files
            keep_count: Keep this many most recent backups
            keep_days: Or keep backups from last N days
            
        Returns:
            Result with number of deleted files
        """
        pattern = f"{self.backup_dir}/{self.ctx.namespace}_{name_pattern}_*"
        
        if keep_count:
            # Keep N most recent, delete rest
            cmd = f"ls -t {pattern} 2>/dev/null | tail -n +{keep_count + 1} | xargs -r rm -f"
        elif keep_days:
            # Delete files older than N days
            cmd = f"find {self.backup_dir} -name '{self.ctx.namespace}_{name_pattern}_*' -mtime +{keep_days} -delete"
        else:
            return Result.fail("Must specify keep_count or keep_days")
        
        code, stdout, stderr = self._exec(cmd, server)
        
        if code == 0:
            return Result.ok("Old backups rotated")
        else:
            return Result.fail(stderr.strip())
    
    def list_backups(
        self,
        name_pattern: Optional[str] = None,
        server: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List available backups.
        
        Args:
            name_pattern: Filter by name pattern
            
        Returns:
            List of backup info dicts
        """
        pattern = f"{self.backup_dir}/{self.ctx.namespace}_"
        if name_pattern:
            pattern += f"{name_pattern}_"
        pattern += "*"
        
        cmd = f"ls -lh {pattern} 2>/dev/null"
        code, stdout, _ = self._exec(cmd, server)
        
        if code != 0:
            return []
        
        backups = []
        for line in stdout.strip().split("\n"):
            if not line or line.startswith("total"):
                continue
            
            parts = line.split()
            if len(parts) >= 9:
                backups.append({
                    "path": parts[-1],
                    "size": parts[4],
                    "date": " ".join(parts[5:8]),
                    "name": os.path.basename(parts[-1]),
                })
        
        return backups
