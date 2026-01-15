"""
HTTP error types.

Unified error hierarchy for all HTTP operations.
Allows consistent error handling across sync and async clients.

Usage:
    from http import HttpError, RateLimitError, TimeoutError
    
    try:
        response = await client.get("https://api.example.com")
    except RateLimitError as e:
        print(f"Rate limited, retry after {e.retry_after}s")
    except TimeoutError as e:
        print(f"Request timed out after {e.timeout}s")
    except HttpError as e:
        print(f"HTTP error {e.status_code}: {e.message}")
"""

from typing import Optional, Dict, Any


class HttpError(Exception):
    """
    Base HTTP error.
    
    All HTTP-related errors inherit from this.
    
    Attributes:
        message: Human-readable error message
        status_code: HTTP status code (if applicable)
        url: Request URL
        method: HTTP method
        response_body: Raw response body (if available)
    """
    
    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        url: Optional[str] = None,
        method: Optional[str] = None,
        response_body: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        self.message = message
        self.status_code = status_code
        self.url = url
        self.method = method
        self.response_body = response_body
        self.headers = headers
        super().__init__(message)
    
    def __str__(self) -> str:
        parts = [self.message]
        if self.status_code:
            parts.insert(0, f"[{self.status_code}]")
        if self.method and self.url:
            parts.append(f"({self.method} {self.url})")
        return " ".join(parts)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": type(self).__name__,
            "message": self.message,
            "status_code": self.status_code,
            "url": self.url,
            "method": self.method,
        }


class ConnectionError(HttpError):
    """Failed to establish connection."""
    pass


class TimeoutError(HttpError):
    """Request timed out."""
    
    def __init__(
        self,
        message: str = "Request timed out",
        timeout: Optional[float] = None,
        **kwargs,
    ):
        self.timeout = timeout
        if timeout:
            message = f"{message} after {timeout}s"
        super().__init__(message, **kwargs)


class RateLimitError(HttpError):
    """Rate limit exceeded (429)."""
    
    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: Optional[float] = None,
        **kwargs,
    ):
        self.retry_after = retry_after
        if retry_after:
            message = f"{message}, retry after {retry_after}s"
        super().__init__(message, status_code=429, **kwargs)


class AuthenticationError(HttpError):
    """Authentication failed (401)."""
    
    def __init__(self, message: str = "Authentication failed", **kwargs):
        super().__init__(message, status_code=401, **kwargs)


class AuthorizationError(HttpError):
    """Authorization failed (403)."""
    
    def __init__(self, message: str = "Access forbidden", **kwargs):
        super().__init__(message, status_code=403, **kwargs)


class NotFoundError(HttpError):
    """Resource not found (404)."""
    
    def __init__(self, message: str = "Resource not found", **kwargs):
        super().__init__(message, status_code=404, **kwargs)


class ValidationError(HttpError):
    """Request validation failed (400, 422)."""
    
    def __init__(
        self,
        message: str = "Validation failed",
        errors: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        self.errors = errors
        super().__init__(message, **kwargs)


class ServerError(HttpError):
    """Server-side error (5xx)."""
    
    def __init__(self, message: str = "Server error", **kwargs):
        super().__init__(message, **kwargs)


class CircuitOpenError(HttpError):
    """Circuit breaker is open, request not attempted."""
    
    def __init__(
        self,
        message: str = "Circuit breaker open",
        service: Optional[str] = None,
        **kwargs,
    ):
        self.service = service
        if service:
            message = f"{message} for service '{service}'"
        super().__init__(message, **kwargs)


def raise_for_status(
    status_code: int,
    response_body: Optional[str] = None,
    url: Optional[str] = None,
    method: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
) -> None:
    """
    Raise appropriate error for HTTP status code.
    
    Args:
        status_code: HTTP status code
        response_body: Response body for error details
        url: Request URL
        method: HTTP method
        headers: Response headers
        
    Raises:
        HttpError subclass based on status code
    """
    if status_code < 400:
        return
    
    # Common args for all errors
    kwargs = {
        "url": url,
        "method": method,
        "response_body": response_body,
        "headers": headers,
        "status_code": status_code,
    }
    
    # Try to extract message from response
    message = _extract_error_message(response_body) or f"HTTP {status_code}"
    
    # Map status codes to error types
    if status_code == 401:
        raise AuthenticationError(message, **kwargs)
    elif status_code == 403:
        raise AuthorizationError(message, **kwargs)
    elif status_code == 404:
        raise NotFoundError(message, **kwargs)
    elif status_code == 429:
        retry_after = _parse_retry_after(headers)
        raise RateLimitError(message, retry_after=retry_after, **kwargs)
    elif status_code in (400, 422):
        errors = _extract_validation_errors(response_body)
        raise ValidationError(message, errors=errors, **kwargs)
    elif status_code >= 500:
        raise ServerError(message, **kwargs)
    else:
        raise HttpError(message, **kwargs)


def _extract_error_message(response_body: Optional[str]) -> Optional[str]:
    """Try to extract error message from response body."""
    if not response_body:
        return None
    
    try:
        import json
        data = json.loads(response_body)
        
        # Common patterns
        for key in ("message", "error", "detail", "error_message", "msg"):
            if key in data:
                value = data[key]
                if isinstance(value, str):
                    return value
                if isinstance(value, dict) and "message" in value:
                    return value["message"]
        
        # Nested error object
        if "error" in data and isinstance(data["error"], dict):
            return data["error"].get("message")
        
    except (json.JSONDecodeError, TypeError):
        # Return truncated body as message
        if len(response_body) < 200:
            return response_body
    
    return None


def _parse_retry_after(headers: Optional[Dict[str, str]]) -> Optional[float]:
    """Parse Retry-After header."""
    if not headers:
        return None
    
    retry_after = headers.get("Retry-After") or headers.get("retry-after")
    if not retry_after:
        return None
    
    try:
        return float(retry_after)
    except ValueError:
        # Could be a date, ignore for now
        return None


def _extract_validation_errors(response_body: Optional[str]) -> Optional[Dict[str, Any]]:
    """Extract validation errors from response body."""
    if not response_body:
        return None
    
    try:
        import json
        data = json.loads(response_body)
        
        # Common patterns
        for key in ("errors", "detail", "validation_errors", "fields"):
            if key in data:
                return data[key]
        
    except (json.JSONDecodeError, TypeError):
        pass
    
    return None
