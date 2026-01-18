"""
LLM API errors.

Provides consistent error handling across all LLM providers.
"""

from __future__ import annotations
from typing import Optional, Dict, Any


class LLMError(Exception):
    """Base error for LLM API calls."""
    
    def __init__(
        self,
        message: str,
        provider: str = "unknown",
        status_code: Optional[int] = None,
        response_body: Optional[Dict[str, Any]] = None,
    ):
        self.message = message
        self.provider = provider
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(f"[{provider}] {message}")
    
    def __repr__(self) -> str:
        return f"LLMError(provider={self.provider!r}, status={self.status_code}, message={self.message!r})"


class LLMRateLimitError(LLMError):
    """Rate limit exceeded."""
    
    def __init__(
        self,
        message: str = "Rate limit exceeded",
        provider: str = "unknown",
        retry_after: Optional[float] = None,
        **kwargs,
    ):
        self.retry_after = retry_after
        super().__init__(message, provider, status_code=429, **kwargs)


class LLMAuthError(LLMError):
    """Authentication failed."""
    
    def __init__(
        self,
        message: str = "Authentication failed",
        provider: str = "unknown",
        **kwargs,
    ):
        super().__init__(message, provider, status_code=401, **kwargs)


class LLMContextLengthError(LLMError):
    """Context length exceeded."""
    
    def __init__(
        self,
        message: str = "Context length exceeded",
        provider: str = "unknown",
        max_tokens: Optional[int] = None,
        **kwargs,
    ):
        self.max_tokens = max_tokens
        super().__init__(message, provider, status_code=400, **kwargs)


class LLMContentFilterError(LLMError):
    """Content filtered by safety systems."""
    
    def __init__(
        self,
        message: str = "Content filtered",
        provider: str = "unknown",
        **kwargs,
    ):
        super().__init__(message, provider, status_code=400, **kwargs)


class LLMTimeoutError(LLMError):
    """Request timed out."""
    
    def __init__(
        self,
        message: str = "Request timed out",
        provider: str = "unknown",
        timeout: Optional[float] = None,
        **kwargs,
    ):
        self.timeout = timeout
        super().__init__(message, provider, status_code=None, **kwargs)


class LLMConnectionError(LLMError):
    """Failed to connect to LLM service."""
    
    def __init__(
        self,
        message: str = "Connection failed",
        provider: str = "unknown",
        **kwargs,
    ):
        super().__init__(message, provider, status_code=None, **kwargs)
