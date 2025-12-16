# Attachments Module

Generic file attachment handling with multiple storage backends.

## Features

- **Multiple backends**: Local filesystem, S3, S3-compatible (MinIO, DO Spaces)
- **Metadata handling**: Automatic MIME type detection, dimensions, checksums
- **Presigned URLs**: Secure download links with expiration
- **Async operations**: Fully async API

## Installation

Already part of shared_lib. Dependencies:

```bash
pip install aiofiles          # For local storage
pip install aioboto3          # For S3 storage
pip install pillow            # Optional: for image dimensions
```

## Quick Start

### Local Storage

```python
from shared_lib.attachments import LocalStore, Attachment

# Create store
store = LocalStore(base_path="/data/uploads")

# Save from file path
attachment = Attachment.from_path("/tmp/document.pdf")
path = await store.save(attachment)
# Returns: "2025/01/15/a1b2c3d4e5f6.pdf"

# Save from bytes
attachment = Attachment.from_bytes(
    content=file_bytes,
    file_name="report.pdf",
)
path = await store.save(attachment, entity_type="reports", entity_id="123")
# Returns: "reports/123/f7g8h9i0j1k2.pdf"

# Load
content = await store.load(path)

# Get URL
url = await store.get_url(path)

# Delete
await store.delete(path)
```

### S3 Storage

```python
from shared_lib.attachments import S3Store

# AWS S3
store = S3Store.aws(
    bucket="my-bucket",
    access_key=config.aws_access_key,
    secret_key=config.aws_secret_key,
    region="us-east-1",
)

# DigitalOcean Spaces
store = S3Store.digitalocean(
    space="my-space",
    region="nyc3",  # nyc3, sfo3, ams3, sgp1, fra1, etc.
    access_key=config.do_spaces_key,
    secret_key=config.do_spaces_secret,
)

# MinIO
store = S3Store.minio(
    bucket="my-bucket",
    access_key=config.minio_access_key,
    secret_key=config.minio_secret_key,
    endpoint="localhost:9000",
    secure=False,  # True for HTTPS
)

# Or use base constructor directly
store = S3Store(
    bucket="my-bucket",
    access_key=config.access_key,
    secret_key=config.secret_key,
    region="us-east-1",
)

# Usage is identical to LocalStore
attachment = Attachment.from_bytes(image_bytes, "photo.jpg")
path = await store.save(attachment)

# Get presigned URL (expires in 1 hour)
url = await store.get_url(path, expires_in=3600)
```

**Note:** Credentials are always required. No magic env var fallbacks - your app decides where to get credentials from (env vars, secrets vault, config file, etc.).

## Creating Attachments

### From File Path

```python
# Basic
attachment = Attachment.from_path("/path/to/file.pdf")

# With options
attachment = Attachment.from_path(
    "/path/to/image.jpg",
    file_name="custom_name.jpg",     # Override filename
    load_content=True,                # Load into memory
    compute_checksum=True,            # Compute MD5
)

# Image dimensions are extracted automatically
print(attachment.width, attachment.height)
```

### From Bytes

```python
attachment = Attachment.from_bytes(
    content=file_bytes,
    file_name="document.pdf",
    file_type="application/pdf",      # Optional, guessed from filename
    compute_checksum=True,
)
```

### From Upload (FastAPI)

```python
from fastapi import UploadFile

@app.post("/upload")
async def upload(file: UploadFile):
    attachment = Attachment.from_upload(
        file=file.file,
        file_name=file.filename,
        file_type=file.content_type,
    )
    path = await store.save(attachment)
    return {"path": path}
```

## Organizing Files

### By Entity

```python
# Organize by entity type and ID
path = await store.save(
    attachment,
    entity_type="messages",
    entity_id="msg_123",
)
# Result: "messages/msg_123/a1b2c3d4.pdf"
```

### Custom Path

```python
# Use custom path
path = await store.save(
    attachment,
    path="avatars/user_456.jpg",
)
```

### Auto-Generated Path

```python
# Default: organized by date
path = await store.save(attachment)
# Result: "2025/01/15/a1b2c3d4.pdf"
```

## Metadata

### Automatic Metadata

```python
attachment = Attachment.from_path("photo.jpg", compute_checksum=True)

print(attachment.file_name)   # "photo.jpg"
print(attachment.file_type)   # "image/jpeg"
print(attachment.file_size)   # 1234567
print(attachment.checksum)    # "d41d8cd98f00b204e9800998ecf8427e"
print(attachment.width)       # 1920 (for images)
print(attachment.height)      # 1080 (for images)
```

### Get Stored Metadata

```python
metadata = await store.get_metadata(path)

print(metadata.file_name)
print(metadata.file_type)
print(metadata.file_size)
print(metadata.created_at)
```

## URLs

### Presigned URLs (S3)

```python
# Default: 1 hour expiration
url = await store.get_url(path)

# Custom expiration
url = await store.get_url(path, expires_in=86400)  # 24 hours

# Force download
url = await store.get_url(
    path,
    content_disposition='attachment; filename="report.pdf"',
)
```

### Public URLs

```python
# S3 with CloudFront CDN
store = S3Store.aws(
    bucket="my-bucket",
    access_key=config.aws_access_key,
    secret_key=config.aws_secret_key,
    region="us-east-1",
    public_url_base="https://d1234.cloudfront.net",
)
url = await store.get_url(path)
# Returns: "https://d1234.cloudfront.net/path/to/file.pdf"

# DigitalOcean Spaces with CDN
store = S3Store.digitalocean(
    space="my-space",
    region="nyc3",
    access_key=config.do_spaces_key,
    secret_key=config.do_spaces_secret,
    public_url_base="https://my-space.nyc3.cdn.digitaloceanspaces.com",
)

# Local with web server
store = LocalStore(
    base_path="/var/www/uploads",
    url_base="https://files.example.com/uploads",
)
url = await store.get_url(path)
# Returns: "https://files.example.com/uploads/path/to/file.pdf"
```

## Type Checking

```python
if attachment.is_image:
    # Process image
    thumbnail = create_thumbnail(attachment)

if attachment.is_pdf:
    # Extract text
    text = extract_pdf_text(attachment)

if attachment.is_document:
    # Includes PDF, Word, Excel, etc.
    index_document(attachment)
```

## Error Handling

```python
from shared_lib.attachments.base import (
    AttachmentNotFoundError,
    AttachmentTooLargeError,
    InvalidAttachmentTypeError,
)

try:
    content = await store.load(path)
except AttachmentNotFoundError:
    return {"error": "File not found"}
```

## Advanced Usage

### List Files

```python
# List all files
files = await store.list_files()

# List with prefix
files = await store.list_files(prefix="messages/")

# Get total size
size = await store.get_total_size(prefix="user_123/")
```

### Copy/Move

```python
# Copy
new_path = await store.copy(source_path, dest_path)

# Move
new_path = await store.move(source_path, dest_path)

# S3-native copy (more efficient)
new_path = await s3_store.copy_object(source_path, dest_path)
```

## Integration Example

### With Entity Framework

```python
from shared_lib.attachments import LocalStore, Attachment

store = LocalStore("/data/uploads")

async def add_attachment(message_id: str, file: UploadFile, get_connection):
    attachment = Attachment.from_upload(file.file, file.filename)
    
    # Save file to storage
    path = await store.save(
        attachment,
        entity_type="messages",
        entity_id=message_id,
    )
    
    # Update message entity
    conn = await get_connection()
    message = await conn.get_entity("messages", message_id)
    
    message["attachment_path"] = path
    message["attachment_type"] = attachment.file_type
    message["attachment_size"] = attachment.file_size
    
    await conn.save_entity("messages", message)
    
    return path

async def get_attachment_url(message_id: str, get_connection):
    conn = await get_connection()
    message = await conn.get_entity("messages", message_id)
    
    if message and message.get("attachment_path"):
        return await store.get_url(message["attachment_path"])
    return None
```

## API Reference

### `Attachment`

| Property | Type | Description |
|----------|------|-------------|
| file_name | str | Original filename |
| file_type | str | MIME type |
| file_size | int | Size in bytes |
| content | bytes | File content (if loaded) |
| checksum | str | MD5 checksum |
| width | int | Image width (if image) |
| height | int | Image height (if image) |
| is_image | bool | True if image |
| is_pdf | bool | True if PDF |
| extension | str | File extension |

### `AttachmentStore`

| Method | Description |
|--------|-------------|
| `save(attachment, path?, entity_id?, entity_type?)` | Save attachment |
| `load(path)` | Load content |
| `delete(path)` | Delete attachment |
| `exists(path)` | Check existence |
| `get_url(path, expires_in?, content_disposition?)` | Get download URL |
| `get_metadata(path)` | Get metadata |
| `copy(source, dest)` | Copy attachment |
| `move(source, dest)` | Move attachment |
| `list_files(prefix?)` | List files |
| `get_total_size(prefix?)` | Get total size |
