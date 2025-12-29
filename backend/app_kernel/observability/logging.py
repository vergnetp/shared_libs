"""
Structured logging - Kernel logging interface.

Wraps the existing log module (if available) and provides structured logging
with request/job context. Falls back to standard library logging.

Usage:
    from app_kernel.observability import get_logger, log_context
    
    logger = get_logger()
    
    # Log with context
    with log_context(request_id="123", user_id="456"):
        logger.info("Processing request")
    
    # Or explicitly
    logger.info("Processing", request_id="123", user_id="456")
"""
from typing import Optional, Dict, Any
from contextlib import contextmanager
import threading
import logging

# Try to import from existing log module, fall back to stdlib
try:
    from ..log import Logger, LoggerConfig, LogLevel
    from ..log.logging import debug, info, warning, error, critical
    LOG_MODULE_AVAILABLE = True
except ImportError:
    LOG_MODULE_AVAILABLE = False
    Logger = None
    LoggerConfig = None
    LogLevel = None


# Thread-local context
_context = threading.local()


def _get_context() -> Dict[str, Any]:
    """Get current thread's logging context."""
    if not hasattr(_context, 'data'):
        _context.data = {}
    return _context.data


def _set_context(data: Dict[str, Any]):
    """Set current thread's logging context."""
    _context.data = data


@contextmanager
def log_context(**kwargs):
    """
    Context manager for adding context to logs.
    
    Usage:
        with log_context(request_id="123", user_id="456"):
            logger.info("Processing")  # Includes request_id and user_id
    """
    old_context = _get_context().copy()
    new_context = {**old_context, **kwargs}
    _set_context(new_context)
    try:
        yield
    finally:
        _set_context(old_context)


class StdlibLogger:
    """
    Fallback logger using standard library logging.
    """
    
    def __init__(self, name: str = "app_kernel"):
        self._logger = logging.getLogger(name)
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
            ))
            self._logger.addHandler(handler)
            self._logger.setLevel(logging.INFO)
    
    @property
    def logger(self):
        """For compatibility with code expecting .logger attribute."""
        return self._logger
    
    def _format_message(self, message: str, kwargs: Dict[str, Any]) -> str:
        """Format message with context."""
        ctx = _get_context()
        all_ctx = {**ctx, **kwargs}
        if all_ctx:
            ctx_str = " ".join(f"{k}={v}" for k, v in all_ctx.items())
            return f"{message} | {ctx_str}"
        return message
    
    def debug(self, message: str, **kwargs):
        self._logger.debug(self._format_message(message, kwargs))
    
    def info(self, message: str, **kwargs):
        self._logger.info(self._format_message(message, kwargs))
    
    def warning(self, message: str, **kwargs):
        self._logger.warning(self._format_message(message, kwargs))
    
    def error(self, message: str, **kwargs):
        self._logger.error(self._format_message(message, kwargs))
    
    def critical(self, message: str, **kwargs):
        self._logger.critical(self._format_message(message, kwargs))
    
    def set_level(self, level: str):
        level_map = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "critical": logging.CRITICAL,
        }
        self._logger.setLevel(level_map.get(level.lower(), logging.INFO))


class KernelLogger:
    """
    Kernel logger with automatic context injection.
    
    Wraps the existing Logger (if available) and adds context from log_context().
    """
    
    def __init__(self, logger = None):
        """
        Initialize kernel logger.
        
        Args:
            logger: Underlying Logger instance. If None, uses singleton.
        """
        self._logger = logger
    
    @property
    def logger(self):
        if self._logger is None:
            if LOG_MODULE_AVAILABLE:
                self._logger = Logger.get_instance()
            else:
                return None
        return self._logger
    
    def _merge_context(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Merge thread context with explicit kwargs."""
        ctx = _get_context()
        return {**ctx, **kwargs}
    
    def debug(self, message: str, **kwargs):
        """Log debug message with context."""
        if LOG_MODULE_AVAILABLE:
            from ..log.logging import debug as log_debug
            log_debug(message, **self._merge_context(kwargs))
        elif self._logger:
            self._logger.debug(message, **self._merge_context(kwargs))
    
    def info(self, message: str, **kwargs):
        """Log info message with context."""
        if LOG_MODULE_AVAILABLE:
            from ..log.logging import info as log_info
            log_info(message, **self._merge_context(kwargs))
        elif self._logger:
            self._logger.info(message, **self._merge_context(kwargs))
    
    def warning(self, message: str, **kwargs):
        """Log warning message with context."""
        if LOG_MODULE_AVAILABLE:
            from ..log.logging import warning as log_warning
            log_warning(message, **self._merge_context(kwargs))
        elif self._logger:
            self._logger.warning(message, **self._merge_context(kwargs))
    
    def error(self, message: str, **kwargs):
        """Log error message with context."""
        if LOG_MODULE_AVAILABLE:
            from ..log.logging import error as log_error
            log_error(message, **self._merge_context(kwargs))
        elif self._logger:
            self._logger.error(message, **self._merge_context(kwargs))
    
    def critical(self, message: str, **kwargs):
        """Log critical message with context."""
        if LOG_MODULE_AVAILABLE:
            from ..log.logging import critical as log_critical
            log_critical(message, **self._merge_context(kwargs))
        elif self._logger:
            self._logger.critical(message, **self._merge_context(kwargs))
    
    def set_level(self, level: str):
        """Set logging level."""
        if LOG_MODULE_AVAILABLE and self.logger:
            self.logger.set_log_level(level)
        elif self._logger and hasattr(self._logger, 'set_level'):
            self._logger.set_level(level)


# Module-level logger
_kernel_logger: Optional[KernelLogger] = None


def init_kernel_logger(config = None) -> KernelLogger:
    """Initialize the kernel logger. Called by init_app_kernel()."""
    global _kernel_logger
    
    if LOG_MODULE_AVAILABLE and config:
        logger = Logger(config)
        _kernel_logger = KernelLogger(logger)
    elif LOG_MODULE_AVAILABLE:
        logger = Logger.get_instance()
        _kernel_logger = KernelLogger(logger)
    else:
        # Use stdlib fallback
        stdlib_logger = StdlibLogger("app_kernel")
        _kernel_logger = KernelLogger(stdlib_logger)
    
    return _kernel_logger


def get_logger() -> KernelLogger:
    """Get the kernel logger."""
    global _kernel_logger
    if _kernel_logger is None:
        if LOG_MODULE_AVAILABLE:
            _kernel_logger = KernelLogger()
        else:
            _kernel_logger = KernelLogger(StdlibLogger("app_kernel"))
    return _kernel_logger
