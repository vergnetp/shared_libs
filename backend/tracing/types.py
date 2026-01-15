"""
Tracing type definitions.

Enums and type hints for the tracing system.
"""

from enum import Enum
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field


class SpanKind(Enum):
    """Type of operation being traced."""
    INTERNAL = "internal"      # Internal function call
    HTTP_CLIENT = "http_client"  # Outbound HTTP request
    HTTP_SERVER = "http_server"  # Inbound HTTP request
    DATABASE = "database"      # Database query
    CACHE = "cache"            # Cache operation
    QUEUE = "queue"            # Message queue operation
    

class SpanStatus(Enum):
    """Outcome of a traced operation."""
    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class SpanAttributes:
    """
    Standard attributes for spans.
    
    Following OpenTelemetry semantic conventions where applicable.
    """
    # HTTP attributes
    http_method: Optional[str] = None
    http_url: Optional[str] = None
    http_status_code: Optional[int] = None
    http_request_body_size: Optional[int] = None
    http_response_body_size: Optional[int] = None
    
    # Database attributes
    db_system: Optional[str] = None  # postgres, mysql, sqlite
    db_name: Optional[str] = None
    db_operation: Optional[str] = None  # SELECT, INSERT, etc.
    db_statement: Optional[str] = None  # Truncated query
    
    # Error attributes
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    
    # Custom attributes
    custom: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to flat dictionary, excluding None values."""
        result = {}
        for key, value in self.__dict__.items():
            if value is not None:
                if key == "custom":
                    result.update(value)
                else:
                    result[key] = value
        return result
