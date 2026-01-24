"""
Backup service for stateful services.

Orchestrates backup/restore operations across PostgreSQL, MySQL, Redis, MongoDB.
"""

import os
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass

from .storage import BackupStorage, BackupFile, create_storage


@dataclass
class BackupResult:
    """Result of a backup operation."""
    success: bool
    service_id: str
    service_type: str
    filename: Optional[str] = None
    size_bytes: Optional[int] = None
    storage_type: Optional[str] = None
    storage_path: Optional[str] = None
    error: Optional[str] = None
    duration_ms: Optional[int] = None
    
    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "service_id": self.service_id,
            "service_type": self.service_type,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "storage_type": self.storage_type,
            "storage_path": self.storage_path,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


@dataclass  
class RestoreResult:
    """Result of a restore operation."""
    success: bool
    service_id: str
    backup_id: str
    error: Optional[str] = None
    duration_ms: Optional[int] = None
    
    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "service_id": self.service_id,
            "backup_id": self.backup_id,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


class BackupService:
    """
    Service for backing up and restoring stateful services.
    
    Usage:
        from infra.backup import BackupService
        from infra.node_agent import NodeAgentClient
        
        backup_svc = BackupService()
        
        # Backup a postgres service
        result = await backup_svc.backup_service(
            agent=NodeAgentClient(...),
            service_id="svc-123",
            service_type="postgres",
            container_name="postgres-prod",
            workspace_id="ws-456",
            project="myapp",
            service_name="postgres",
            config={"database": "mydb", "user": "admin"},
        )
        
        # Restore from backup
        result = await backup_svc.restore_service(
            agent=NodeAgentClient(...),
            backup_id="backup-789",
            db=db_connection,  # For loading backup metadata
        )
    """
    
    # File extensions by service type
    EXTENSIONS = {
        "postgres": ".sql.gz",
        "mysql": ".sql.gz",
        "redis": ".rdb",
        "mongodb": ".archive.gz",
    }
    
    def __init__(
        self,
        storage: BackupStorage = None,
        retention_count: int = None,
    ):
        """
        Initialize backup service.
        
        Args:
            storage: Storage backend (auto-created if not provided)
            retention_count: Number of backups to retain per service (default: 7)
        """
        self.storage = storage or create_storage()
        self.retention_count = retention_count or int(
            os.environ.get("BACKUP_RETENTION_COUNT", "7")
        )
    
    def _generate_filename(self, service_type: str, service_name: str) -> str:
        """Generate backup filename with timestamp."""
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        ext = self.EXTENSIONS.get(service_type, ".backup")
        return f"{service_name}_{timestamp}{ext}"
    
    async def backup_service(
        self,
        agent,  # NodeAgentClient
        service_id: str,
        service_type: str,
        container_name: str,
        workspace_id: str,
        project: str,
        service_name: str,
        config: Dict[str, Any] = None,
        log_fn: Callable[[str], None] = None,
    ) -> BackupResult:
        """
        Backup a stateful service.
        
        Args:
            agent: NodeAgentClient connected to the server
            service_id: Service entity ID
            service_type: One of: postgres, mysql, redis, mongodb
            container_name: Docker container name
            workspace_id: Workspace ID for path organization
            project: Project name
            service_name: Service name (used in filename)
            config: Service-specific config (database, user, password)
            log_fn: Optional callback for logging progress
            
        Returns:
            BackupResult with success status and metadata
        """
        config = config or {}
        log = log_fn or (lambda x: None)
        start = datetime.utcnow()
        
        log(f"Starting backup of {service_type} service: {service_name}")
        
        try:
            # Execute backup based on service type
            if service_type == "postgres":
                success, data = await agent.backup_postgres(
                    container_name=container_name,
                    database=config.get("database", "postgres"),
                    user=config.get("user", "postgres"),
                )
            elif service_type == "mysql":
                success, data = await agent.backup_mysql(
                    container_name=container_name,
                    database=config.get("database"),
                    user=config.get("user", "root"),
                    password=config.get("password"),
                )
            elif service_type == "redis":
                success, data = await agent.backup_redis(
                    container_name=container_name,
                )
            elif service_type == "mongodb":
                success, data = await agent.backup_mongodb(
                    container_name=container_name,
                    database=config.get("database"),
                )
            else:
                return BackupResult(
                    success=False,
                    service_id=service_id,
                    service_type=service_type,
                    error=f"Unsupported service type: {service_type}",
                )
            
            if not success:
                error_msg = data if isinstance(data, str) else "Backup failed"
                log(f"❌ Backup failed: {error_msg}")
                return BackupResult(
                    success=False,
                    service_id=service_id,
                    service_type=service_type,
                    error=error_msg,
                )
            
            # Save to storage
            filename = self._generate_filename(service_type, service_name)
            log(f"📦 Saving backup: {filename} ({len(data)} bytes)")
            
            backup_file = await self.storage.save(
                data=data,
                workspace_id=workspace_id,
                project=project,
                service=service_name,
                filename=filename,
            )
            
            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            log(f"✅ Backup completed in {duration}ms")
            
            return BackupResult(
                success=True,
                service_id=service_id,
                service_type=service_type,
                filename=backup_file.filename,
                size_bytes=backup_file.size_bytes,
                storage_type=backup_file.storage_type,
                storage_path=backup_file.path,
                duration_ms=duration,
            )
            
        except Exception as e:
            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            log(f"❌ Backup error: {e}")
            return BackupResult(
                success=False,
                service_id=service_id,
                service_type=service_type,
                error=str(e),
                duration_ms=duration,
            )
    
    async def restore_service(
        self,
        agent,  # NodeAgentClient
        backup_record: Dict[str, Any],  # From database
        container_name: str,
        config: Dict[str, Any] = None,
        log_fn: Callable[[str], None] = None,
    ) -> RestoreResult:
        """
        Restore a stateful service from backup.
        
        Args:
            agent: NodeAgentClient connected to the server
            backup_record: Backup record from database with:
                - id, service_id, service_type, storage_path, storage_type
            container_name: Target Docker container name
            config: Service-specific config (database, user, password)
            log_fn: Optional callback for logging progress
            
        Returns:
            RestoreResult with success status
        """
        config = config or {}
        log = log_fn or (lambda x: None)
        start = datetime.utcnow()
        
        backup_id = backup_record["id"]
        service_id = backup_record["service_id"]
        service_type = backup_record["service_type"]
        storage_path = backup_record["storage_path"]
        
        log(f"Starting restore of {service_type} from backup {backup_id}")
        
        try:
            # Load backup data from storage
            log(f"📥 Loading backup from: {storage_path}")
            backup_data = await self.storage.load(storage_path)
            log(f"📦 Loaded {len(backup_data)} bytes")
            
            # Execute restore based on service type
            if service_type == "postgres":
                result = await agent.restore_postgres(
                    container_name=container_name,
                    backup_data=backup_data,
                    database=config.get("database", "postgres"),
                    user=config.get("user", "postgres"),
                )
            elif service_type == "mysql":
                result = await agent.restore_mysql(
                    container_name=container_name,
                    backup_data=backup_data,
                    database=config.get("database"),
                    user=config.get("user", "root"),
                    password=config.get("password"),
                )
            elif service_type == "redis":
                result = await agent.restore_redis(
                    container_name=container_name,
                    backup_data=backup_data,
                )
            elif service_type == "mongodb":
                result = await agent.restore_mongodb(
                    container_name=container_name,
                    backup_data=backup_data,
                    database=config.get("database"),
                )
            else:
                return RestoreResult(
                    success=False,
                    service_id=service_id,
                    backup_id=backup_id,
                    error=f"Unsupported service type: {service_type}",
                )
            
            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            
            if result.success:
                log(f"✅ Restore completed in {duration}ms")
                return RestoreResult(
                    success=True,
                    service_id=service_id,
                    backup_id=backup_id,
                    duration_ms=duration,
                )
            else:
                log(f"❌ Restore failed: {result.error}")
                return RestoreResult(
                    success=False,
                    service_id=service_id,
                    backup_id=backup_id,
                    error=result.error,
                    duration_ms=duration,
                )
                
        except FileNotFoundError:
            return RestoreResult(
                success=False,
                service_id=service_id,
                backup_id=backup_id,
                error=f"Backup file not found: {storage_path}",
            )
        except Exception as e:
            duration = int((datetime.utcnow() - start).total_seconds() * 1000)
            log(f"❌ Restore error: {e}")
            return RestoreResult(
                success=False,
                service_id=service_id,
                backup_id=backup_id,
                error=str(e),
                duration_ms=duration,
            )
    
    async def cleanup_old_backups(
        self,
        workspace_id: str,
        project: str,
        service: str,
        keep_count: int = None,
        log_fn: Callable[[str], None] = None,
    ) -> int:
        """
        Delete old backups exceeding retention count.
        
        Args:
            workspace_id: Workspace ID
            project: Project name
            service: Service name
            keep_count: Number to keep (default: self.retention_count)
            log_fn: Optional logging callback
            
        Returns:
            Number of backups deleted
        """
        keep = keep_count or self.retention_count
        log = log_fn or (lambda x: None)
        
        # List existing backups
        backups = await self.storage.list_backups(
            workspace_id=workspace_id,
            project=project,
            service=service,
        )
        
        if len(backups) <= keep:
            return 0
        
        # Sort by creation time, oldest first
        backups.sort(key=lambda b: b.created_at)
        
        # Delete oldest backups
        to_delete = backups[:-keep]
        deleted = 0
        
        for backup in to_delete:
            try:
                await self.storage.delete(backup.path)
                log(f"🗑️ Deleted old backup: {backup.filename}")
                deleted += 1
            except Exception as e:
                log(f"⚠️ Failed to delete {backup.filename}: {e}")
        
        return deleted
    
    async def list_backups(
        self,
        workspace_id: str,
        project: str = None,
        service: str = None,
    ) -> List[BackupFile]:
        """
        List available backups.
        
        Args:
            workspace_id: Workspace ID
            project: Optional project filter
            service: Optional service filter
            
        Returns:
            List of BackupFile objects
        """
        return await self.storage.list_backups(
            workspace_id=workspace_id,
            project=project,
            service=service,
        )
