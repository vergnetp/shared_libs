"""
Core types for file attachments.
"""

import mimetypes
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, BinaryIO, Union


@dataclass
class AttachmentMetadata:
    """
    Metadata about an attachment.
    Stored alongside the file or in database.
    """
    file_name: str
    file_type: str  # MIME type
    file_size: int  # Bytes
    
    # Optional
    checksum: Optional[str] = None  # MD5 or SHA256
    
    # For images
    width: Optional[int] = None
    height: Optional[int] = None
    
    # For documents
    page_count: Optional[int] = None
    
    # Custom
    custom: dict[str, Any] = field(default_factory=dict)
    
    created_at: Optional[datetime] = None
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "file_name": self.file_name,
            "file_type": self.file_type,
            "file_size": self.file_size,
            "checksum": self.checksum,
            "width": self.width,
            "height": self.height,
            "page_count": self.page_count,
            "custom": self.custom,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AttachmentMetadata":
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        
        return cls(
            file_name=data["file_name"],
            file_type=data["file_type"],
            file_size=data["file_size"],
            checksum=data.get("checksum"),
            width=data.get("width"),
            height=data.get("height"),
            page_count=data.get("page_count"),
            custom=data.get("custom") or {},
            created_at=created_at,
        )


@dataclass
class Attachment:
    """
    Represents a file attachment.
    
    Can be created from:
    - File path
    - Bytes content
    - File-like object
    """
    file_name: str
    file_type: str  # MIME type
    file_size: int = 0
    
    # Content (one of these)
    content: Optional[bytes] = None
    file_path: Optional[str] = None
    
    # Metadata
    checksum: Optional[str] = None
    
    # For images
    width: Optional[int] = None
    height: Optional[int] = None
    
    # Custom metadata
    metadata: dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def from_path(
        cls,
        path: Union[Path, str],
        *,
        file_name: str = None,
        file_type: str = None,
        load_content: bool = False,
        compute_checksum: bool = False,
    ) -> "Attachment":
        """
        Create attachment from file path.
        
        Args:
            path: Path to file
            file_name: Override filename (defaults to path.name)
            file_type: Override MIME type (defaults to guessed)
            load_content: Load file content into memory
            compute_checksum: Compute MD5 checksum
        """
        path = Path(path)
        
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        
        if file_type is None:
            file_type, _ = mimetypes.guess_type(str(path))
            file_type = file_type or "application/octet-stream"
        
        stat = path.stat()
        content = None
        checksum = None
        
        if load_content or compute_checksum:
            content = path.read_bytes()
            if compute_checksum:
                checksum = hashlib.md5(content).hexdigest()
            if not load_content:
                content = None
        
        attachment = cls(
            file_name=file_name or path.name,
            file_type=file_type,
            file_size=stat.st_size,
            content=content,
            file_path=str(path),
            checksum=checksum,
        )
        
        # Try to get image dimensions
        if file_type and file_type.startswith("image/"):
            attachment._extract_image_dimensions(path)
        
        return attachment
    
    @classmethod
    def from_bytes(
        cls,
        content: bytes,
        file_name: str,
        file_type: str = None,
        compute_checksum: bool = True,
    ) -> "Attachment":
        """
        Create attachment from bytes content.
        
        Args:
            content: File content
            file_name: Filename
            file_type: MIME type (guessed from filename if not provided)
            compute_checksum: Compute MD5 checksum
        """
        if file_type is None:
            file_type, _ = mimetypes.guess_type(file_name)
            file_type = file_type or "application/octet-stream"
        
        checksum = None
        if compute_checksum:
            checksum = hashlib.md5(content).hexdigest()
        
        return cls(
            file_name=file_name,
            file_type=file_type,
            file_size=len(content),
            content=content,
            checksum=checksum,
        )
    
    @classmethod
    def from_upload(
        cls,
        file: BinaryIO,
        file_name: str,
        file_type: str = None,
        compute_checksum: bool = True,
    ) -> "Attachment":
        """
        Create attachment from file-like object (e.g., FastAPI UploadFile).
        
        Args:
            file: File-like object
            file_name: Filename
            file_type: MIME type
            compute_checksum: Compute MD5 checksum
        """
        content = file.read()
        return cls.from_bytes(
            content=content,
            file_name=file_name,
            file_type=file_type,
            compute_checksum=compute_checksum,
        )
    
    async def get_content(self) -> bytes:
        """Get file content, loading from path if needed."""
        if self.content is not None:
            return self.content
        
        if self.file_path:
            return Path(self.file_path).read_bytes()
        
        raise ValueError("Attachment has no content or file_path")
    
    def _extract_image_dimensions(self, path: Path):
        """Extract width/height from image file."""
        try:
            from PIL import Image
            with Image.open(path) as img:
                self.width, self.height = img.size
        except ImportError:
            pass  # PIL not available
        except Exception:
            pass  # Not a valid image or other error
    
    def get_metadata(self) -> AttachmentMetadata:
        """Convert to AttachmentMetadata."""
        return AttachmentMetadata(
            file_name=self.file_name,
            file_type=self.file_type,
            file_size=self.file_size,
            checksum=self.checksum,
            width=self.width,
            height=self.height,
            custom=self.metadata,
        )
    
    @property
    def extension(self) -> str:
        """Get file extension (with dot)."""
        ext = mimetypes.guess_extension(self.file_type)
        if ext:
            return ext
        
        # Fallback to filename
        if "." in self.file_name:
            return "." + self.file_name.rsplit(".", 1)[1].lower()
        
        return ""
    
    @property
    def is_image(self) -> bool:
        """Check if attachment is an image."""
        return self.file_type.startswith("image/")
    
    @property
    def is_pdf(self) -> bool:
        """Check if attachment is a PDF."""
        return self.file_type == "application/pdf"
    
    @property
    def is_document(self) -> bool:
        """Check if attachment is a document (PDF, Word, etc.)."""
        doc_types = [
            "application/pdf",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ]
        return self.file_type in doc_types


# Common MIME types
MIME_TYPES = {
    # Images
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    
    # Documents
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    
    # Text
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
    ".xml": "application/xml",
    ".html": "text/html",
    ".md": "text/markdown",
    
    # Archives
    ".zip": "application/zip",
    ".tar": "application/x-tar",
    ".gz": "application/gzip",
}


def guess_mime_type(filename: str) -> str:
    """Guess MIME type from filename."""
    ext = "." + filename.rsplit(".", 1)[1].lower() if "." in filename else ""
    return MIME_TYPES.get(ext) or mimetypes.guess_type(filename)[0] or "application/octet-stream"
