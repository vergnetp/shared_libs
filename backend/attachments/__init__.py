"""
File attachment handling with multiple storage backends.

Provides a generic way to store, retrieve, and manage file attachments
for any entity (messages, documents, users, etc.)

Usage:
    from shared_lib.attachments import (
        Attachment,
        AttachmentStore,
        LocalStore,
        S3Store,
    )
    
    # Create store
    store = LocalStore(base_path="/data/uploads")
    # or
    store = S3Store(bucket="my-bucket", prefix="uploads/")
    
    # Save attachment
    attachment = Attachment.from_file(path_to_file)
    stored_path = await store.save(attachment, content)
    
    # Load attachment
    content = await store.load(stored_path)
    
    # Get download URL
    url = await store.get_url(stored_path, expires_in=3600)
"""

from .types import Attachment, AttachmentMetadata

from .base import AttachmentStore

from .local import LocalStore

from .s3 import S3Store

__all__ = [
    # Types
    "Attachment",
    "AttachmentMetadata",
    # Stores
    "AttachmentStore",
    "LocalStore",
    "S3Store",
]
