import math
from typing import Any, Dict, Optional, Callable
from enum import Enum


class RetryStrategy(str, Enum):
    """Retry strategy types."""
    FIXED = "fixed"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"


class QueueRetryConfig:
    """
    Configuration for retry behavior.
    
    Controls how failed jobs are retried including delay calculation,
    maximum attempts, and backoff strategies.
    """
    def __init__(
        self,
        max_attempts: int = 3,
        strategy: RetryStrategy = RetryStrategy.EXPONENTIAL,
        min_delay: float = 1.0,
        max_delay: float = 300.0,
        base_delay: float = 5.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
        jitter_factor: float = 0.1,
        retry_on: Optional[tuple] = None
    ):
        """
        Initialize retry configuration.
        
        Args:
            max_attempts: Maximum retry attempts
            strategy: Retry strategy (fixed, linear, exponential)
            min_delay: Minimum delay between retries in seconds
            max_delay: Maximum delay between retries in seconds
            base_delay: Base delay for calculations
            exponential_base: Base for exponential backoff
            jitter: Whether to add random jitter
            jitter_factor: Jitter as fraction of delay
            retry_on: Tuple of exception types to retry on (None = all)
        """
        self._max_attempts = max_attempts
        self._strategy = strategy
        self._min_delay = min_delay
        self._max_delay = max_delay
        self._base_delay = base_delay
        self._exponential_base = exponential_base
        self._jitter = jitter
        self._jitter_factor = jitter_factor
        self._retry_on = retry_on or (Exception,)
        self._validate_config()
    
    @property
    def max_attempts(self) -> int:
        return self._max_attempts
    
    @property
    def strategy(self) -> RetryStrategy:
        return self._strategy
    
    @property
    def min_delay(self) -> float:
        return self._min_delay
    
    @property
    def max_delay(self) -> float:
        return self._max_delay
    
    @property
    def base_delay(self) -> float:
        return self._base_delay
    
    @property
    def exponential_base(self) -> float:
        return self._exponential_base
    
    @property
    def jitter(self) -> bool:
        return self._jitter
    
    @property
    def jitter_factor(self) -> float:
        return self._jitter_factor
    
    @property
    def retry_on(self) -> tuple:
        return self._retry_on
    
    def _validate_config(self):
        """Validate configuration."""
        if self._max_attempts < 0:
            raise ValueError("max_attempts must be non-negative")
        if self._min_delay < 0:
            raise ValueError("min_delay must be non-negative")
        if self._max_delay < self._min_delay:
            raise ValueError("max_delay must be >= min_delay")
    
    def calculate_delay(self, attempt: int) -> float:
        """
        Calculate delay for a given attempt number.
        
        Args:
            attempt: Current attempt number (1-indexed)
            
        Returns:
            Delay in seconds
        """
        import random
        
        if self._strategy == RetryStrategy.FIXED:
            delay = self._base_delay
        elif self._strategy == RetryStrategy.LINEAR:
            delay = self._base_delay * attempt
        elif self._strategy == RetryStrategy.EXPONENTIAL:
            delay = self._base_delay * (self._exponential_base ** (attempt - 1))
        else:
            delay = self._base_delay
        
        # Clamp to min/max
        delay = max(self._min_delay, min(delay, self._max_delay))
        
        # Add jitter
        if self._jitter:
            jitter_range = delay * self._jitter_factor
            delay += random.uniform(-jitter_range, jitter_range)
            delay = max(self._min_delay, delay)
        
        return delay
    
    def should_retry(self, attempt: int, exception: Exception) -> bool:
        """
        Determine if a job should be retried.
        
        Args:
            attempt: Current attempt number
            exception: The exception that occurred
            
        Returns:
            True if should retry, False otherwise
        """
        if attempt >= self._max_attempts:
            return False
        return isinstance(exception, self._retry_on)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return {
            "max_attempts": self._max_attempts,
            "strategy": self._strategy.value,
            "min_delay": self._min_delay,
            "max_delay": self._max_delay,
            "base_delay": self._base_delay,
            "exponential_base": self._exponential_base,
            "jitter": self._jitter,
            "jitter_factor": self._jitter_factor
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'QueueRetryConfig':
        """Create instance from dictionary."""
        strategy = data.get('strategy', 'exponential')
        if isinstance(strategy, str):
            strategy = RetryStrategy(strategy)
        
        return cls(
            max_attempts=data.get('max_attempts', 3),
            strategy=strategy,
            min_delay=data.get('min_delay', 1.0),
            max_delay=data.get('max_delay', 300.0),
            base_delay=data.get('base_delay', 5.0),
            exponential_base=data.get('exponential_base', 2.0),
            jitter=data.get('jitter', True),
            jitter_factor=data.get('jitter_factor', 0.1)
        )
    
    @classmethod
    def exponential(
        cls,
        max_attempts: int = 3,
        min_delay: float = 1.0,
        max_delay: float = 300.0,
        base_delay: float = 5.0
    ) -> 'QueueRetryConfig':
        """Create exponential backoff configuration."""
        return cls(
            max_attempts=max_attempts,
            strategy=RetryStrategy.EXPONENTIAL,
            min_delay=min_delay,
            max_delay=max_delay,
            base_delay=base_delay
        )
    
    @classmethod
    def fixed(
        cls,
        max_attempts: int = 3,
        delay: float = 5.0
    ) -> 'QueueRetryConfig':
        """Create fixed delay configuration."""
        return cls(
            max_attempts=max_attempts,
            strategy=RetryStrategy.FIXED,
            base_delay=delay,
            min_delay=delay,
            max_delay=delay
        )
