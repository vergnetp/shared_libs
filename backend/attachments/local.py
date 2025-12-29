"""
Local filesystem storage backend.
"""

import aiofiles
import aiofiles.os
import json
from pathlib import Path
from typing import Optional, Union
from urllib.parse import quote

from .base import AttachmentStore, AttachmentNotFoundError
from .types import Attachment, AttachmentMetadata


class LocalStore(AttachmentStore):
    """
    Local filesystem storage backend.
    
    Stores files in a directory structure with optional metadata JSON files.
    
    Usage:
        store = LocalStore(base_path="/data/uploads")
        
        # With URL base for serving files
        store = LocalStore(
            base_path="/data/uploads",
            url_base="https://cdn.example.com/uploads",
        )
    """
    
    def __init__(
        self,
        base_path: Union[Path, str],
        url_base: str = None,
        store_metadata: bool = True,
        create_dirs: bool = True,
    ):
        """
        Initialize local storage.
        
        Args:
            base_path: Base directory for storing files
            url_base: Base URL for generating download URLs
            store_metadata: Store metadata in .json files alongside attachments
            create_dirs: Create directories as needed
        """
        self.base_path = Path(base_path)
        self.url_base = url_base.rstrip("/") if url_base else None
        self.store_metadata = store_metadata
        self.create_dirs = create_dirs
        
        if create_dirs:
            self.base_path.mkdir(parents=True, exist_ok=True)
    
    def _full_path(self, path: str) -> Path:
        """Get full filesystem path."""
        return self.base_path / path
    
    def _metadata_path(self, path: str) -> Path:
        """Get metadata file path."""
        return self._full_path(path).with_suffix(self._full_path(path).suffix + ".meta.json")
    
    async def save(
        self,
        attachment: Attachment,
        *,
        path: str = None,
        entity_id: str = None,
        entity_type: str = None,
    ) -> str:
        """Save an attachment to local filesystem."""
        # Generate path if not provided
        if path is None:
            path = self.generate_path(
                attachment,
                entity_id=entity_id,
                entity_type=entity_type,
            )
        
        full_path = self._full_path(path)
        
        # Create directory if needed
        if self.create_dirs:
            full_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Get content
        content = await attachment.get_content()
        
        # Write file
        async with aiofiles.open(full_path, "wb") as f:
            await f.write(content)
        
        # Write metadata
        if self.store_metadata:
            metadata = attachment.get_metadata()
            metadata.created_at = metadata.created_at or __import__("datetime").datetime.utcnow()
            
            meta_path = self._metadata_path(path)
            async with aiofiles.open(meta_path, "w") as f:
                await f.write(json.dumps(metadata.to_dict(), indent=2))
        
        return path
    
    async def load(self, path: str) -> bytes:
        """Load attachment content from local filesystem."""
        full_path = self._full_path(path)
        
        if not full_path.exists():
            raise AttachmentNotFoundError(path)
        
        async with aiofiles.open(full_path, "rb") as f:
            return await f.read()
    
    async def delete(self, path: str) -> bool:
        """Delete an attachment from local filesystem."""
        full_path = self._full_path(path)
        
        if not full_path.exists():
            return False
        
        # Delete file
        await aiofiles.os.remove(full_path)
        
        # Delete metadata
        meta_path = self._metadata_path(path)
        if meta_path.exists():
            await aiofiles.os.remove(meta_path)
        
        return True
    
    async def exists(self, path: str) -> bool:
        """Check if an attachment exists."""
        return self._full_path(path).exists()
    
    async def get_url(
        self,
        path: str,
        expires_in: int = 3600,
        content_disposition: str = None,
    ) -> str:
        """
        Get a URL for downloading the attachment.
        
        For local storage, returns a file:// URL or HTTP URL if url_base is set.
        Note: expires_in is ignored for local storage.
        """
        full_path = self._full_path(path)
        
        if not full_path.exists():
            raise AttachmentNotFoundError(path)
        
        if self.url_base:
            # Return HTTP URL
            url = f"{self.url_base}/{quote(path)}"
            if content_disposition:
                url += f"?cd={quote(content_disposition)}"
            return url
        else:
            # Return file:// URL
            return full_path.as_uri()
    
    async def get_metadata(self, path: str) -> Optional[AttachmentMetadata]:
        """Get attachment metadata."""
        meta_path = self._metadata_path(path)
        
        if not meta_path.exists():
            # Fall back to basic metadata from file
            full_path = self._full_path(path)
            if not full_path.exists():
                return None
            
            from .types import guess_mime_type
            stat = full_path.stat()
            return AttachmentMetadata(
                file_name=full_path.name,
                file_type=guess_mime_type(full_path.name),
                file_size=stat.st_size,
            )
        
        async with aiofiles.open(meta_path, "r") as f:
            data = json.loads(await f.read())
            return AttachmentMetadata.from_dict(data)
    
    async def list_files(
        self,
        prefix: str = "",
        recursive: bool = True,
    ) -> list[str]:
        """
        List files in storage.
        
        Args:
            prefix: Path prefix to filter
            recursive: Search recursively
            
        Returns:
            List of file paths
        """
        base = self._full_path(prefix)
        
        if not base.exists():
            return []
        
        files = []
        pattern = "**/*" if recursive else "*"
        
        for path in base.glob(pattern):
            if path.is_file() and not path.name.endswith(".meta.json"):
                rel_path = path.relative_to(self.base_path)
                files.append(str(rel_path))
        
        return files
    
    async def get_total_size(self, prefix: str = "") -> int:
        """Get total size of files in storage."""
        files = await self.list_files(prefix)
        total = 0
        
        for file_path in files:
            full_path = self._full_path(file_path)
            if full_path.exists():
                total += full_path.stat().st_size
        
        return total
