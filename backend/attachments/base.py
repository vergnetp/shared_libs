"""
Abstract base class for attachment storage.
"""

import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from .types import Attachment, AttachmentMetadata


class AttachmentStore(ABC):
    """
    Abstract interface for attachment storage.
    
    Implement this for your storage backend (local filesystem, S3, GCS, etc.)
    """
    
    @abstractmethod
    async def save(
        self,
        attachment: Attachment,
        *,
        path: str = None,
        entity_id: str = None,
        entity_type: str = None,
    ) -> str:
        """
        Save an attachment.
        
        Args:
            attachment: Attachment to save
            path: Optional custom path (auto-generated if not provided)
            entity_id: Optional entity ID for organizing by entity
            entity_type: Optional entity type for organizing by type
            
        Returns:
            Stored path (use this to load/delete later)
        """
        ...
    
    @abstractmethod
    async def load(self, path: str) -> bytes:
        """
        Load attachment content.
        
        Args:
            path: Stored path from save()
            
        Returns:
            File content as bytes
            
        Raises:
            FileNotFoundError: If attachment not found
        """
        ...
    
    @abstractmethod
    async def delete(self, path: str) -> bool:
        """
        Delete an attachment.
        
        Args:
            path: Stored path from save()
            
        Returns:
            True if deleted, False if not found
        """
        ...
    
    @abstractmethod
    async def exists(self, path: str) -> bool:
        """
        Check if an attachment exists.
        
        Args:
            path: Stored path from save()
            
        Returns:
            True if exists
        """
        ...
    
    @abstractmethod
    async def get_url(
        self,
        path: str,
        expires_in: int = 3600,
        content_disposition: str = None,
    ) -> str:
        """
        Get a URL for downloading the attachment.
        
        Args:
            path: Stored path from save()
            expires_in: URL expiration in seconds (for signed URLs)
            content_disposition: Optional Content-Disposition header value
            
        Returns:
            Download URL
        """
        ...
    
    @abstractmethod
    async def get_metadata(self, path: str) -> Optional[AttachmentMetadata]:
        """
        Get attachment metadata.
        
        Args:
            path: Stored path from save()
            
        Returns:
            Metadata if found, None otherwise
        """
        ...
    
    async def copy(self, source_path: str, dest_path: str = None) -> str:
        """
        Copy an attachment.
        
        Args:
            source_path: Source path
            dest_path: Destination path (auto-generated if not provided)
            
        Returns:
            New stored path
        """
        content = await self.load(source_path)
        metadata = await self.get_metadata(source_path)
        
        if metadata:
            attachment = Attachment.from_bytes(
                content=content,
                file_name=metadata.file_name,
                file_type=metadata.file_type,
            )
        else:
            attachment = Attachment.from_bytes(
                content=content,
                file_name="file",
            )
        
        return await self.save(attachment, path=dest_path)
    
    async def move(self, source_path: str, dest_path: str) -> str:
        """
        Move an attachment.
        
        Args:
            source_path: Source path
            dest_path: Destination path
            
        Returns:
            New stored path
        """
        new_path = await self.copy(source_path, dest_path)
        await self.delete(source_path)
        return new_path
    
    def generate_path(
        self,
        attachment: Attachment,
        entity_id: str = None,
        entity_type: str = None,
        prefix: str = "",
    ) -> str:
        """
        Generate a unique storage path for an attachment.
        
        Path format: {prefix}/{entity_type}/{entity_id}/{uuid}{ext}
        Or simpler: {prefix}/{date}/{uuid}{ext}
        """
        ext = attachment.extension or ""
        unique_id = uuid.uuid4().hex[:16]
        date_str = datetime.utcnow().strftime("%Y/%m/%d")
        
        if entity_type and entity_id:
            return f"{prefix}{entity_type}/{entity_id}/{unique_id}{ext}".lstrip("/")
        else:
            return f"{prefix}{date_str}/{unique_id}{ext}".lstrip("/")


class AttachmentNotFoundError(Exception):
    """Raised when an attachment is not found."""
    
    def __init__(self, path: str):
        self.path = path
        super().__init__(f"Attachment not found: {path}")


class AttachmentTooLargeError(Exception):
    """Raised when an attachment exceeds size limit."""
    
    def __init__(self, size: int, max_size: int):
        self.size = size
        self.max_size = max_size
        super().__init__(f"Attachment size {size} exceeds maximum {max_size}")


class InvalidAttachmentTypeError(Exception):
    """Raised when attachment type is not allowed."""
    
    def __init__(self, file_type: str, allowed_types: list[str]):
        self.file_type = file_type
        self.allowed_types = allowed_types
        super().__init__(f"File type {file_type} not allowed. Allowed: {allowed_types}")
