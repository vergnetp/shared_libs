from typing import Any, Dict, Optional


class QueueWorkerConfig:
    """
    Configuration for worker behavior.
    
    Controls worker pool size, timeouts, and execution behavior
    for job processing.
    """
    def __init__(
        self,
        worker_count: int = 3,
        thread_pool_size: int = 10,
        work_timeout: int = 300,
        poll_interval: float = 1.0,
        batch_size: int = 1,
        graceful_shutdown_timeout: int = 30
    ):
        """
        Initialize worker configuration.
        
        Args:
            worker_count: Number of concurrent workers
            thread_pool_size: Size of thread pool for sync tasks
            work_timeout: Max seconds per job before timeout
            poll_interval: Seconds between queue polls
            batch_size: Number of jobs to fetch per poll
            graceful_shutdown_timeout: Seconds to wait for graceful shutdown
        """
        self._worker_count = worker_count
        self._thread_pool_size = thread_pool_size
        self._work_timeout = work_timeout
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._graceful_shutdown_timeout = graceful_shutdown_timeout
        self._validate_config()
    
    @property
    def worker_count(self) -> int:
        return self._worker_count
    
    @property
    def thread_pool_size(self) -> int:
        return self._thread_pool_size
    
    @property
    def work_timeout(self) -> int:
        return self._work_timeout
    
    @property
    def poll_interval(self) -> float:
        return self._poll_interval
    
    @property
    def batch_size(self) -> int:
        return self._batch_size
    
    @property
    def graceful_shutdown_timeout(self) -> int:
        return self._graceful_shutdown_timeout
    
    def _validate_config(self):
        """Validate configuration."""
        if self._worker_count < 1:
            raise ValueError("worker_count must be at least 1")
        if self._thread_pool_size < 1:
            raise ValueError("thread_pool_size must be at least 1")
        if self._work_timeout < 1:
            raise ValueError("work_timeout must be at least 1")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return {
            "worker_count": self._worker_count,
            "thread_pool_size": self._thread_pool_size,
            "work_timeout": self._work_timeout,
            "poll_interval": self._poll_interval,
            "batch_size": self._batch_size,
            "graceful_shutdown_timeout": self._graceful_shutdown_timeout
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'QueueWorkerConfig':
        """Create instance from dictionary."""
        return cls(
            worker_count=data.get('worker_count', 3),
            thread_pool_size=data.get('thread_pool_size', 10),
            work_timeout=data.get('work_timeout', 300),
            poll_interval=data.get('poll_interval', 1.0),
            batch_size=data.get('batch_size', 1),
            graceful_shutdown_timeout=data.get('graceful_shutdown_timeout', 30)
        )
