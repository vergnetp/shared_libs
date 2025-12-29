import time
import threading
from typing import Any, Dict, Optional, Callable, List
from collections import defaultdict


class QueueMetricsConfig:
    """
    Configuration for metrics collection.
    
    Controls what metrics are collected, how they're stored,
    and how they're exported for monitoring.
    """
    def __init__(
        self,
        enabled: bool = True,
        prefix: str = "queue",
        collect_histograms: bool = True,
        histogram_buckets: Optional[List[float]] = None,
        export_interval: int = 60,
        on_metric: Optional[Callable] = None
    ):
        """
        Initialize metrics configuration.
        
        Args:
            enabled: Whether metrics collection is enabled
            prefix: Prefix for metric names
            collect_histograms: Whether to collect timing histograms
            histogram_buckets: Bucket boundaries for histograms
            export_interval: Seconds between metric exports
            on_metric: Callback for metric events
        """
        self._enabled = enabled
        self._prefix = prefix
        self._collect_histograms = collect_histograms
        self._histogram_buckets = histogram_buckets or [
            0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0
        ]
        self._export_interval = export_interval
        self._on_metric = on_metric
        
        # Internal storage
        self._counters: Dict[str, int] = defaultdict(int)
        self._gauges: Dict[str, float] = {}
        self._histograms: Dict[str, List[float]] = defaultdict(list)
        self._lock = threading.Lock()
    
    @property
    def enabled(self) -> bool:
        return self._enabled
    
    @property
    def prefix(self) -> str:
        return self._prefix
    
    @property
    def collect_histograms(self) -> bool:
        return self._collect_histograms
    
    @property
    def histogram_buckets(self) -> List[float]:
        return self._histogram_buckets
    
    @property
    def export_interval(self) -> int:
        return self._export_interval
    
    def increment(self, name: str, value: int = 1, tags: Dict[str, str] = None):
        """Increment a counter metric."""
        if not self._enabled:
            return
        key = self._make_key(name, tags)
        with self._lock:
            self._counters[key] += value
        if self._on_metric:
            self._on_metric("counter", name, value, tags)
    
    def gauge(self, name: str, value: float, tags: Dict[str, str] = None):
        """Set a gauge metric."""
        if not self._enabled:
            return
        key = self._make_key(name, tags)
        with self._lock:
            self._gauges[key] = value
        if self._on_metric:
            self._on_metric("gauge", name, value, tags)
    
    def histogram(self, name: str, value: float, tags: Dict[str, str] = None):
        """Record a histogram metric."""
        if not self._enabled or not self._collect_histograms:
            return
        key = self._make_key(name, tags)
        with self._lock:
            self._histograms[key].append(value)
        if self._on_metric:
            self._on_metric("histogram", name, value, tags)
    
    def timing(self, name: str, tags: Dict[str, str] = None):
        """Context manager for timing operations."""
        return TimingContext(self, name, tags)
    
    def _make_key(self, name: str, tags: Dict[str, str] = None) -> str:
        """Create a unique key for a metric."""
        key = f"{self._prefix}.{name}"
        if tags:
            tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
            key = f"{key}[{tag_str}]"
        return key
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get all current metrics."""
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {k: self._summarize_histogram(v) for k, v in self._histograms.items()}
            }
    
    def _summarize_histogram(self, values: List[float]) -> Dict[str, float]:
        """Summarize histogram values."""
        if not values:
            return {"count": 0, "sum": 0, "min": 0, "max": 0, "avg": 0}
        return {
            "count": len(values),
            "sum": sum(values),
            "min": min(values),
            "max": max(values),
            "avg": sum(values) / len(values)
        }
    
    def reset(self):
        """Reset all metrics."""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return {
            "enabled": self._enabled,
            "prefix": self._prefix,
            "collect_histograms": self._collect_histograms,
            "histogram_buckets": self._histogram_buckets,
            "export_interval": self._export_interval
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'QueueMetricsConfig':
        """Create instance from dictionary."""
        return cls(
            enabled=data.get('enabled', True),
            prefix=data.get('prefix', 'queue'),
            collect_histograms=data.get('collect_histograms', True),
            histogram_buckets=data.get('histogram_buckets'),
            export_interval=data.get('export_interval', 60)
        )


class TimingContext:
    """Context manager for timing operations."""
    
    def __init__(self, metrics: QueueMetricsConfig, name: str, tags: Dict[str, str] = None):
        self.metrics = metrics
        self.name = name
        self.tags = tags
        self.start_time = None
    
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_time
        self.metrics.histogram(self.name, duration, self.tags)
        return False
