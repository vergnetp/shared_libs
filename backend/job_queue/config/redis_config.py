import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse


class QueueRedisConfig:
    """
    Configuration for Redis connection.
    
    Manages Redis connection parameters including URL, connection pooling,
    and retry behavior for the queue system.
    
    Supports two modes:
    1. URL-based: creates its own client from URL (standalone usage)
    2. Injected client: uses a pre-created client (kernel injects fakeredis or shared real client)
    """
    def __init__(
        self,
        url: str = "redis://localhost:6379/0",
        key_prefix: str = "app:",
        max_connections: int = 10,
        socket_timeout: float = 5.0,
        socket_connect_timeout: float = 5.0,
        retry_on_timeout: bool = True,
        health_check_interval: int = 30,
        client=None,
    ):
        """
        Initialize Redis configuration.
        
        Args:
            url: Redis connection URL
            key_prefix: Prefix for all Redis keys
            max_connections: Maximum connections in the pool
            socket_timeout: Socket timeout in seconds
            socket_connect_timeout: Connection timeout in seconds
            retry_on_timeout: Whether to retry on timeout
            health_check_interval: Seconds between health checks
            client: Pre-created Redis client (if provided, URL is ignored for connections)
        """
        self._url = url
        self._key_prefix = key_prefix
        self._max_connections = max_connections
        self._socket_timeout = socket_timeout
        self._socket_connect_timeout = socket_connect_timeout
        self._retry_on_timeout = retry_on_timeout
        self._health_check_interval = health_check_interval
        self._client = client
        self._validate_config()
    
    @property
    def url(self) -> str:
        return self._url
    
    @property
    def key_prefix(self) -> str:
        return self._key_prefix
    
    @property
    def max_connections(self) -> int:
        return self._max_connections
    
    @property
    def socket_timeout(self) -> float:
        return self._socket_timeout
    
    @property
    def socket_connect_timeout(self) -> float:
        return self._socket_connect_timeout
    
    @property
    def retry_on_timeout(self) -> bool:
        return self._retry_on_timeout
    
    @property
    def health_check_interval(self) -> int:
        return self._health_check_interval
    
    def _validate_config(self):
        """Validate configuration."""
        if self._max_connections < 1:
            raise ValueError("max_connections must be at least 1")
        if self._socket_timeout <= 0:
            raise ValueError("socket_timeout must be positive")
    
    def get_client(self):
        """Get or create Redis client with connection pooling."""
        if self._client is None:
            try:
                import redis
                self._client = redis.from_url(
                    self._url,
                    max_connections=self._max_connections,
                    socket_timeout=self._socket_timeout,
                    socket_connect_timeout=self._socket_connect_timeout,
                    retry_on_timeout=self._retry_on_timeout,
                    health_check_interval=self._health_check_interval,
                    decode_responses=True
                )
            except ImportError:
                raise ImportError("redis package required: pip install redis")
        return self._client
    
    def test_connection(self) -> bool:
        """Test Redis connection."""
        try:
            client = self.get_client()
            return client.ping()
        except Exception:
            return False
    
    def get_registry_key(self) -> str:
        """Get the key used to track registered queues."""
        return f"{self._key_prefix}registry"
    
    def get_queue_key(self, queue_name: str, priority: str = "normal") -> str:
        """Get the Redis key for a specific queue."""
        return f"{self._key_prefix}{queue_name}:{priority}"
    
    def get_processing_key(self, queue_name: str) -> str:
        """Get the Redis key for processing items."""
        return f"{self._key_prefix}{queue_name}:processing"
    
    def get_failed_key(self, queue_name: str) -> str:
        """Get the Redis key for failed items."""
        return f"{self._key_prefix}{queue_name}:failed"
    
    def get_scheduled_key(self) -> str:
        """Get the Redis key for scheduled items."""
        return f"{self._key_prefix}scheduled"
    
    def get_job_key(self, job_id: str) -> str:
        """Get the Redis key for a specific job's status."""
        return f"{self._key_prefix}job:{job_id}"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return {
            "url": self._mask_url(self._url),
            "key_prefix": self._key_prefix,
            "max_connections": self._max_connections,
            "socket_timeout": self._socket_timeout,
            "socket_connect_timeout": self._socket_connect_timeout,
            "retry_on_timeout": self._retry_on_timeout,
            "health_check_interval": self._health_check_interval
        }
    
    def _mask_url(self, url: str) -> str:
        """Mask password in URL for logging."""
        if not url:
            return url
        try:
            parsed = urlparse(url)
            if parsed.password:
                return url.replace(parsed.password, "***")
            return url
        except Exception:
            return url
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'QueueRedisConfig':
        """Create instance from dictionary."""
        return cls(
            url=data.get('url', 'redis://localhost:6379/0'),
            key_prefix=data.get('key_prefix', 'app:'),
            max_connections=data.get('max_connections', 10),
            socket_timeout=data.get('socket_timeout', 5.0),
            socket_connect_timeout=data.get('socket_connect_timeout', 5.0),
            retry_on_timeout=data.get('retry_on_timeout', True),
            health_check_interval=data.get('health_check_interval', 30)
        )
