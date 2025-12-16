"""
S3 storage backend.

Supports AWS S3 and S3-compatible services (MinIO, DigitalOcean Spaces, etc.)

Credentials are always required explicitly - no magic env var fallbacks.
"""

import json
from typing import Optional
from datetime import datetime

from .base import AttachmentStore, AttachmentNotFoundError
from .types import Attachment, AttachmentMetadata


class S3Store(AttachmentStore):
    """
    S3 storage backend.
    
    Supports AWS S3 and S3-compatible services.
    Credentials are always required - pass them explicitly.
    
    Usage:
        # AWS S3
        store = S3Store(
            bucket="my-bucket",
            region="us-east-1",
            access_key="AKIA...",
            secret_key="...",
        )
        
        # DigitalOcean Spaces
        store = S3Store.digitalocean(
            space="my-space",
            region="nyc3",
            access_key="DO00...",
            secret_key="...",
        )
        
        # MinIO
        store = S3Store.minio(
            bucket="my-bucket",
            endpoint="localhost:9000",
            access_key="minioadmin",
            secret_key="minioadmin",
        )
    """
    
    def __init__(
        self,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = None,
        endpoint_url: str = None,
        prefix: str = "",
        store_metadata: bool = True,
        public_url_base: str = None,
    ):
        """
        Initialize S3 storage.
        
        Args:
            bucket: S3 bucket name
            access_key: Access key (required)
            secret_key: Secret key (required)
            region: AWS region (e.g., "us-east-1")
            endpoint_url: Custom endpoint URL (for S3-compatible services)
            prefix: Key prefix for all objects
            store_metadata: Store metadata as object metadata
            public_url_base: Base URL for public access (e.g., CloudFront CDN)
        """
        if not access_key or not secret_key:
            raise ValueError("access_key and secret_key are required")
        
        self.bucket = bucket
        self.prefix = prefix.rstrip("/") + "/" if prefix else ""
        self.region = region
        self.endpoint_url = endpoint_url
        self.store_metadata = store_metadata
        self.public_url_base = public_url_base.rstrip("/") if public_url_base else None
        
        self._access_key = access_key
        self._secret_key = secret_key
        
        # Lazy-loaded client
        self._session = None
        self._client_kwargs = None
    
    @classmethod
    def digitalocean(
        cls,
        space: str,
        region: str,
        access_key: str,
        secret_key: str,
        prefix: str = "",
        public_url_base: str = None,
    ) -> "S3Store":
        """
        Create store for DigitalOcean Spaces.
        
        Args:
            space: Space name
            region: Region (nyc3, sfo3, ams3, sgp1, fra1, etc.)
            access_key: Spaces access key (required)
            secret_key: Spaces secret key (required)
            prefix: Key prefix
            public_url_base: CDN URL if enabled
        
        Example:
            store = S3Store.digitalocean(
                space="my-space",
                region="nyc3",
                access_key=config.do_spaces_key,
                secret_key=config.do_spaces_secret,
            )
        """
        return cls(
            bucket=space,
            access_key=access_key,
            secret_key=secret_key,
            region=region,
            endpoint_url=f"https://{region}.digitaloceanspaces.com",
            prefix=prefix,
            public_url_base=public_url_base,
        )
    
    @classmethod
    def minio(
        cls,
        bucket: str,
        access_key: str,
        secret_key: str,
        endpoint: str = "localhost:9000",
        secure: bool = False,
        prefix: str = "",
    ) -> "S3Store":
        """
        Create store for MinIO.
        
        Args:
            bucket: Bucket name
            access_key: Access key (required)
            secret_key: Secret key (required)
            endpoint: MinIO endpoint (host:port)
            secure: Use HTTPS
            prefix: Key prefix
        
        Example:
            store = S3Store.minio(
                bucket="uploads",
                access_key=config.minio_access_key,
                secret_key=config.minio_secret_key,
                endpoint="minio.example.com:9000",
                secure=True,
            )
        """
        protocol = "https" if secure else "http"
        
        return cls(
            bucket=bucket,
            access_key=access_key,
            secret_key=secret_key,
            endpoint_url=f"{protocol}://{endpoint}",
            prefix=prefix,
        )
    
    @classmethod
    def aws(
        cls,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
        prefix: str = "",
        public_url_base: str = None,
    ) -> "S3Store":
        """
        Create store for AWS S3.
        
        Args:
            bucket: S3 bucket name
            access_key: AWS access key (required)
            secret_key: AWS secret key (required)
            region: AWS region
            prefix: Key prefix
            public_url_base: CloudFront URL if using CDN
        
        Example:
            store = S3Store.aws(
                bucket="my-bucket",
                access_key=config.aws_access_key,
                secret_key=config.aws_secret_key,
                region="us-east-1",
            )
        """
        return cls(
            bucket=bucket,
            access_key=access_key,
            secret_key=secret_key,
            region=region,
            prefix=prefix,
            public_url_base=public_url_base,
        )
    
    @property
    def client(self):
        """Lazy-load S3 client."""
        if self._session is None:
            import aioboto3
            
            self._session = aioboto3.Session()
            self._client_kwargs = {
                "aws_access_key_id": self._access_key,
                "aws_secret_access_key": self._secret_key,
            }
            
            if self.region:
                self._client_kwargs["region_name"] = self.region
            if self.endpoint_url:
                self._client_kwargs["endpoint_url"] = self.endpoint_url
        
        return self._session.client("s3", **self._client_kwargs)
    
    def _full_key(self, path: str) -> str:
        """Get full S3 key."""
        return f"{self.prefix}{path}"
    
    async def save(
        self,
        attachment: Attachment,
        *,
        path: str = None,
        entity_id: str = None,
        entity_type: str = None,
    ) -> str:
        """Save an attachment to S3."""
        # Generate path if not provided
        if path is None:
            path = self.generate_path(
                attachment,
                entity_id=entity_id,
                entity_type=entity_type,
            )
        
        key = self._full_key(path)
        content = await attachment.get_content()
        
        # Build metadata
        extra_args = {
            "ContentType": attachment.file_type,
        }
        
        if self.store_metadata:
            metadata = {
                "filename": attachment.file_name,
                "size": str(attachment.file_size),
            }
            if attachment.checksum:
                metadata["checksum"] = attachment.checksum
            if attachment.width:
                metadata["width"] = str(attachment.width)
            if attachment.height:
                metadata["height"] = str(attachment.height)
            
            extra_args["Metadata"] = metadata
        
        # Upload
        async with self.client as s3:
            await s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                **extra_args,
            )
        
        return path
    
    async def load(self, path: str) -> bytes:
        """Load attachment content from S3."""
        key = self._full_key(path)
        
        try:
            async with self.client as s3:
                response = await s3.get_object(Bucket=self.bucket, Key=key)
                async with response["Body"] as stream:
                    return await stream.read()
        except Exception as e:
            if "NoSuchKey" in str(e) or "404" in str(e):
                raise AttachmentNotFoundError(path)
            raise
    
    async def delete(self, path: str) -> bool:
        """Delete an attachment from S3."""
        key = self._full_key(path)
        
        try:
            async with self.client as s3:
                await s3.delete_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False
    
    async def exists(self, path: str) -> bool:
        """Check if an attachment exists in S3."""
        key = self._full_key(path)
        
        try:
            async with self.client as s3:
                await s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False
    
    async def get_url(
        self,
        path: str,
        expires_in: int = 3600,
        content_disposition: str = None,
    ) -> str:
        """
        Get a presigned URL for downloading the attachment.
        
        Args:
            path: Stored path
            expires_in: URL expiration in seconds
            content_disposition: Optional Content-Disposition header
        """
        key = self._full_key(path)
        
        # Use public URL if configured
        if self.public_url_base:
            return f"{self.public_url_base}/{path}"
        
        # Generate presigned URL
        params = {
            "Bucket": self.bucket,
            "Key": key,
        }
        
        if content_disposition:
            params["ResponseContentDisposition"] = content_disposition
        
        async with self.client as s3:
            url = await s3.generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=expires_in,
            )
        
        return url
    
    async def get_metadata(self, path: str) -> Optional[AttachmentMetadata]:
        """Get attachment metadata from S3."""
        key = self._full_key(path)
        
        try:
            async with self.client as s3:
                response = await s3.head_object(Bucket=self.bucket, Key=key)
        except Exception:
            return None
        
        # Extract from S3 response
        metadata = response.get("Metadata", {})
        
        return AttachmentMetadata(
            file_name=metadata.get("filename", path.split("/")[-1]),
            file_type=response.get("ContentType", "application/octet-stream"),
            file_size=response.get("ContentLength", 0),
            checksum=metadata.get("checksum"),
            width=int(metadata["width"]) if "width" in metadata else None,
            height=int(metadata["height"]) if "height" in metadata else None,
            created_at=response.get("LastModified"),
        )
    
    async def list_files(
        self,
        prefix: str = "",
        max_keys: int = 1000,
    ) -> list[str]:
        """
        List files in S3 bucket.
        
        Args:
            prefix: Key prefix to filter
            max_keys: Maximum number of keys to return
            
        Returns:
            List of file paths (without bucket prefix)
        """
        full_prefix = self._full_key(prefix)
        files = []
        
        async with self.client as s3:
            paginator = s3.get_paginator("list_objects_v2")
            
            async for page in paginator.paginate(
                Bucket=self.bucket,
                Prefix=full_prefix,
                MaxKeys=max_keys,
            ):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    # Remove bucket prefix
                    if key.startswith(self.prefix):
                        key = key[len(self.prefix):]
                    files.append(key)
        
        return files
    
    async def get_total_size(self, prefix: str = "") -> int:
        """Get total size of files in S3."""
        full_prefix = self._full_key(prefix)
        total = 0
        
        async with self.client as s3:
            paginator = s3.get_paginator("list_objects_v2")
            
            async for page in paginator.paginate(
                Bucket=self.bucket,
                Prefix=full_prefix,
            ):
                for obj in page.get("Contents", []):
                    total += obj.get("Size", 0)
        
        return total
    
    async def copy_object(
        self,
        source_path: str,
        dest_path: str,
        dest_bucket: str = None,
    ) -> str:
        """
        Copy an object within S3 (more efficient than download/upload).
        
        Args:
            source_path: Source path
            dest_path: Destination path
            dest_bucket: Destination bucket (defaults to same bucket)
            
        Returns:
            Destination path
        """
        source_key = self._full_key(source_path)
        dest_key = self._full_key(dest_path)
        dest_bucket = dest_bucket or self.bucket
        
        async with self.client as s3:
            await s3.copy_object(
                CopySource={"Bucket": self.bucket, "Key": source_key},
                Bucket=dest_bucket,
                Key=dest_key,
            )
        
        return dest_path
