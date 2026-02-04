"""
HTTP response wrapper.

Unified response object for all HTTP clients.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional
import json as json_module


@dataclass
class HttpResponse:
    """
    HTTP response wrapper.
    
    Provides consistent interface regardless of underlying HTTP library.
    
    Attributes:
        status_code: HTTP status code
        headers: Response headers (case-insensitive access)
        body: Raw response body as bytes
        url: Final URL (after redirects)
        method: HTTP method used
        elapsed_ms: Request duration in milliseconds
        http_version: HTTP version used (e.g., "HTTP/1.1", "HTTP/2")
    """
    status_code: int
    headers: Dict[str, str]
    body: bytes
    url: str
    method: str
    elapsed_ms: float = 0.0
    http_version: str = "HTTP/1.1"  # HTTP version (HTTP/1.1, HTTP/2, HTTP/3)
    
    # Metadata
    retry_count: int = 0
    from_cache: bool = False
    
    @property
    def ok(self) -> bool:
        """Check if response is successful (2xx)."""
        return 200 <= self.status_code < 300
    
    @property
    def is_redirect(self) -> bool:
        """Check if response is a redirect (3xx)."""
        return 300 <= self.status_code < 400
    
    @property
    def is_client_error(self) -> bool:
        """Check if response is client error (4xx)."""
        return 400 <= self.status_code < 500
    
    @property
    def is_server_error(self) -> bool:
        """Check if response is server error (5xx)."""
        return self.status_code >= 500
    
    @property
    def text(self) -> str:
        """Get response body as text."""
        return self.body.decode('utf-8', errors='replace')
    
    @property
    def content(self) -> bytes:
        """Alias for body (requests/httpx compatibility)."""
        return self.body
    
    def json(self) -> Any:
        """
        Parse response body as JSON.
        
        Raises:
            json.JSONDecodeError: If body is not valid JSON
        """
        return json_module.loads(self.body)
    
    def json_or_none(self) -> Optional[Any]:
        """Parse response body as JSON, returning None on failure."""
        try:
            return self.json()
        except (json_module.JSONDecodeError, UnicodeDecodeError):
            return None
    
    def header(self, name: str, default: str = None) -> Optional[str]:
        """
        Get header value (case-insensitive).
        
        Args:
            name: Header name
            default: Default value if not found
        """
        # Try exact match first
        if name in self.headers:
            return self.headers[name]
        
        # Case-insensitive search
        name_lower = name.lower()
        for key, value in self.headers.items():
            if key.lower() == name_lower:
                return value
        
        return default
    
    @property
    def content_type(self) -> Optional[str]:
        """Get Content-Type header."""
        return self.header("Content-Type")
    
    @property
    def content_length(self) -> Optional[int]:
        """Get Content-Length header as int."""
        value = self.header("Content-Length")
        if value:
            try:
                return int(value)
            except ValueError:
                pass
        return None
    
    def raise_for_status(self) -> None:
        """
        Raise exception if response is an error.
        
        Raises:
            HttpError: If status code >= 400
        """
        from .errors import raise_for_status
        raise_for_status(
            self.status_code,
            response_body=self.text if self.body else None,
            url=self.url,
            method=self.method,
            headers=self.headers,
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary (for logging/debugging)."""
        return {
            "status_code": self.status_code,
            "url": self.url,
            "method": self.method,
            "elapsed_ms": self.elapsed_ms,
            "http_version": self.http_version,
            "content_length": len(self.body),
            "headers": dict(self.headers),
        }
    
    def __repr__(self) -> str:
        return f"HttpResponse({self.status_code}, {self.method} {self.url}, {self.http_version}, {len(self.body)} bytes)"
