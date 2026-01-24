"""
Backup storage backends.

Supports local filesystem and DigitalOcean Spaces (S3-compatible).
"""

import os
import asyncio
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass

# Try to import boto3 for Spaces support
try:
    import boto3
    from botocore.config import Config as BotoConfig
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False


@dataclass
class BackupFile:
    """Metadata for a backup file."""
    filename: str
    path: str
    size_bytes: int
    created_at: datetime
    storage_type: str  # "local" or "spaces"
    
    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "path": self.path,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at.isoformat(),
            "storage_type": self.storage_type,
        }


class BackupStorage(ABC):
    """Abstract base class for backup storage."""
    
    @abstractmethod
    async def save(
        self,
        data: bytes,
        workspace_id: str,
        project: str,
        service: str,
        filename: str,
    ) -> BackupFile:
        """Save backup data and return metadata."""
        pass
    
    @abstractmethod
    async def load(self, path: str) -> bytes:
        """Load backup data from path."""
        pass
    
    @abstractmethod
    async def delete(self, path: str) -> bool:
        """Delete backup file."""
        pass
    
    @abstractmethod
    async def list_backups(
        self,
        workspace_id: str,
        project: str = None,
        service: str = None,
    ) -> List[BackupFile]:
        """List backup files with optional filtering."""
        pass
    
    @abstractmethod
    def get_storage_type(self) -> str:
        """Return storage type identifier."""
        pass


class LocalStorage(BackupStorage):
    """Local filesystem backup storage."""
    
    def __init__(self, base_path: str = "/data/backups"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
    
    def _get_path(
        self,
        workspace_id: str,
        project: str,
        service: str,
        filename: str,
    ) -> Path:
        """Build full path for backup file."""
        return self.base_path / workspace_id / project / service / filename
    
    async def save(
        self,
        data: bytes,
        workspace_id: str,
        project: str,
        service: str,
        filename: str,
    ) -> BackupFile:
        """Save backup data to local filesystem."""
        path = self._get_path(workspace_id, project, service, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write in thread pool to not block
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, path.write_bytes, data)
        
        return BackupFile(
            filename=filename,
            path=str(path.relative_to(self.base_path)),
            size_bytes=len(data),
            created_at=datetime.utcnow(),
            storage_type="local",
        )
    
    async def load(self, path: str) -> bytes:
        """Load backup data from local filesystem."""
        full_path = self.base_path / path
        if not full_path.exists():
            raise FileNotFoundError(f"Backup not found: {path}")
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, full_path.read_bytes)
    
    async def delete(self, path: str) -> bool:
        """Delete backup file from local filesystem."""
        full_path = self.base_path / path
        if full_path.exists():
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, full_path.unlink)
            return True
        return False
    
    async def list_backups(
        self,
        workspace_id: str,
        project: str = None,
        service: str = None,
    ) -> List[BackupFile]:
        """List backup files in local filesystem."""
        search_path = self.base_path / workspace_id
        if project:
            search_path = search_path / project
            if service:
                search_path = search_path / service
        
        if not search_path.exists():
            return []
        
        backups = []
        loop = asyncio.get_event_loop()
        
        def scan_dir():
            files = []
            for path in search_path.rglob("*"):
                if path.is_file():
                    stat = path.stat()
                    files.append(BackupFile(
                        filename=path.name,
                        path=str(path.relative_to(self.base_path)),
                        size_bytes=stat.st_size,
                        created_at=datetime.fromtimestamp(stat.st_mtime),
                        storage_type="local",
                    ))
            return files
        
        return await loop.run_in_executor(None, scan_dir)
    
    def get_storage_type(self) -> str:
        return "local"


class SpacesStorage(BackupStorage):
    """DigitalOcean Spaces backup storage (S3-compatible)."""
    
    def __init__(
        self,
        access_key: str,
        secret_key: str,
        region: str = "lon1",
        bucket: str = "deploy-api-backups",
    ):
        if not HAS_BOTO3:
            raise ImportError("boto3 required for Spaces storage: pip install boto3")
        
        self.region = region
        self.bucket = bucket
        self.endpoint = f"https://{region}.digitaloceanspaces.com"
        
        self.client = boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=BotoConfig(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "adaptive"},
            ),
        )
        
        # Ensure bucket exists
        self._ensure_bucket()
    
    def _ensure_bucket(self):
        """Create bucket if it doesn't exist."""
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except self.client.exceptions.ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code == "404":
                # Create bucket
                self.client.create_bucket(
                    Bucket=self.bucket,
                    CreateBucketConfiguration={
                        "LocationConstraint": self.region,
                    },
                )
    
    def _get_key(
        self,
        workspace_id: str,
        project: str,
        service: str,
        filename: str,
    ) -> str:
        """Build S3 key for backup file."""
        return f"{workspace_id}/{project}/{service}/{filename}"
    
    async def save(
        self,
        data: bytes,
        workspace_id: str,
        project: str,
        service: str,
        filename: str,
    ) -> BackupFile:
        """Save backup data to DO Spaces."""
        key = self._get_key(workspace_id, project, service, filename)
        
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType="application/octet-stream",
            ),
        )
        
        return BackupFile(
            filename=filename,
            path=key,
            size_bytes=len(data),
            created_at=datetime.utcnow(),
            storage_type="spaces",
        )
    
    async def load(self, path: str) -> bytes:
        """Load backup data from DO Spaces."""
        loop = asyncio.get_event_loop()
        
        def download():
            response = self.client.get_object(Bucket=self.bucket, Key=path)
            return response["Body"].read()
        
        try:
            return await loop.run_in_executor(None, download)
        except self.client.exceptions.NoSuchKey:
            raise FileNotFoundError(f"Backup not found: {path}")
    
    async def delete(self, path: str) -> bool:
        """Delete backup file from DO Spaces."""
        loop = asyncio.get_event_loop()
        
        def do_delete():
            self.client.delete_object(Bucket=self.bucket, Key=path)
            return True
        
        try:
            return await loop.run_in_executor(None, do_delete)
        except Exception:
            return False
    
    async def list_backups(
        self,
        workspace_id: str,
        project: str = None,
        service: str = None,
    ) -> List[BackupFile]:
        """List backup files in DO Spaces."""
        prefix = workspace_id
        if project:
            prefix = f"{prefix}/{project}"
            if service:
                prefix = f"{prefix}/{service}"
        prefix = f"{prefix}/"
        
        loop = asyncio.get_event_loop()
        
        def list_objects():
            backups = []
            paginator = self.client.get_paginator("list_objects_v2")
            
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    filename = key.split("/")[-1]
                    backups.append(BackupFile(
                        filename=filename,
                        path=key,
                        size_bytes=obj["Size"],
                        created_at=obj["LastModified"].replace(tzinfo=None),
                        storage_type="spaces",
                    ))
            return backups
        
        return await loop.run_in_executor(None, list_objects)
    
    def get_storage_type(self) -> str:
        return "spaces"


def create_storage(
    spaces_key: str = None,
    spaces_secret: str = None,
    region: str = None,
    local_path: str = "/data/backups",
) -> BackupStorage:
    """
    Factory function to create appropriate storage backend.
    
    If Spaces credentials are provided, uses SpacesStorage.
    Otherwise falls back to LocalStorage.
    
    Args:
        spaces_key: DO Spaces access key
        spaces_secret: DO Spaces secret key
        region: DO region (defaults to DEPLOY_API_REGION env or lon1)
        local_path: Path for local storage fallback
        
    Returns:
        BackupStorage instance
    """
    # Check for Spaces credentials
    spaces_key = spaces_key or os.environ.get("BACKUP_SPACES_KEY")
    spaces_secret = spaces_secret or os.environ.get("BACKUP_SPACES_SECRET")
    
    if spaces_key and spaces_secret:
        region = region or os.environ.get("DEPLOY_API_REGION", "lon1")
        try:
            return SpacesStorage(
                access_key=spaces_key,
                secret_key=spaces_secret,
                region=region,
            )
        except Exception as e:
            # Log warning and fall back to local
            import logging
            logging.warning(f"Failed to initialize Spaces storage: {e}, using local")
    
    return LocalStorage(base_path=local_path)
