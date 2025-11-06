# backend/infra/logger.py
import re
import threading
from datetime import datetime
from pathlib import Path


INDENT = 2


class Logger:
    """
    Thread-safe logging utility with sensitive data redaction.
    
    Automatically redacts:
    - DigitalOcean API tokens
    - GitHub personal access tokens
    - Generic API keys
    - Passwords and credentials in JSON
    - Docker Hub passwords
    
    Thread Safety:
    - Uses RLock for reentrant locking
    - Per-thread indentation tracking
    - Thread-safe file writes
    """
    
    # Thread safety
    _lock = threading.RLock()
    _thread_offsets = {}
    
    # State
    offset = 0
    log_file = None
    start_time = None
    
    # Patterns to redact (compiled for performance)
    SENSITIVE_PATTERNS = [
        # DigitalOcean tokens (dop_v1_...)
        (re.compile(r'dop_v1_[a-f0-9]{64}'), 'dop_v1_[REDACTED]'),
        
        # GitHub PAT (ghp_...)
        (re.compile(r'ghp_[a-zA-Z0-9]{36,255}'), 'ghp_[REDACTED]'),
        
        # Generic API keys (32+ alphanumeric)
        (re.compile(r'\b[A-Za-z0-9]{32,}\b'), '[REDACTED_KEY]'),
        
        # JSON credentials
        (re.compile(r'"digitalocean_token"\s*:\s*"[^"]+"'), '"digitalocean_token": "[REDACTED]"'),
        (re.compile(r'"docker_hub_password"\s*:\s*"[^"]+"'), '"docker_hub_password": "[REDACTED]"'),
        (re.compile(r'"password"\s*:\s*"[^"]+"'), '"password": "[REDACTED]"'),
        (re.compile(r'"api_key"\s*:\s*"[^"]+"'), '"api_key": "[REDACTED]"'),
        (re.compile(r'"token"\s*:\s*"[^"]+"'), '"token": "[REDACTED]"'),
        (re.compile(r'"secret"\s*:\s*"[^"]+"'), '"secret": "[REDACTED]"'),
        
        # Authorization headers
        (re.compile(r'Authorization:\s*Bearer\s+\S+'), 'Authorization: Bearer [REDACTED]'),
        (re.compile(r'X-API-Key:\s*\S+'), 'X-API-Key: [REDACTED]'),
    ]
    
    @staticmethod
    def _get_log_file():
        """Get or create log file (thread-safe)"""
        with Logger._lock:
            if Logger.log_file is None:
                try:
                    log_dir = Path("C:\\logs")
                    log_dir.mkdir(exist_ok=True)
                    
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    Logger.log_file = log_dir / f"deployment_{timestamp}.log"
                    
                    with open(Logger.log_file, 'w', encoding='utf-8') as f:
                        f.write(f"=== Deployment Log Started at {datetime.now().isoformat()} ===\n")
                except Exception as e:
                    print(f'Cannot create log file: {e}')
            return Logger.log_file
    
    @staticmethod
    def _redact_sensitive_data(msg: str) -> str:
        """
        Redact sensitive data from log messages.
        
        Args:
            msg: Original log message
            
        Returns:
            Redacted log message with sensitive data masked
        """
        redacted = msg
        
        for pattern, replacement in Logger.SENSITIVE_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        
        return redacted
    
    @staticmethod
    def log(msg: str, redact: bool = True):
        """
        Log a message with optional sensitive data redaction (thread-safe).
        
        Args:
            msg: Message to log
            redact: If True, redact sensitive data (default: True)
        """
        thread_id = threading.current_thread().ident
        thread_name = threading.current_thread().name
        
        # Redact sensitive data
        if redact:
            msg = Logger._redact_sensitive_data(msg)
        
        with Logger._lock:
            # Get per-thread indentation
            offset = Logger._thread_offsets.get(thread_id, 0)
            
            # Include thread name for parallel operations
            if thread_name.startswith("ThreadPoolExecutor"):
                prefix = f"[Thread-{thread_id % 1000}] "
            else:
                prefix = ""
            
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            indented_msg = f"[{timestamp}] {' ' * offset}{prefix}{msg}"
            
            # Console output
            print(indented_msg)
            
            # File output (thread-safe)
            try:
                log_file = Logger._get_log_file()
                if log_file:
                    with open(log_file, 'a', encoding='utf-8') as f:
                        f.write(f"{indented_msg}\n")
                        f.flush()
            except Exception as e:
                print(f"LOG FILE ERROR: {e}")
    
    @staticmethod
    def start():
        """Mark start of operation for timing (thread-safe with per-thread indentation)"""
        thread_id = threading.current_thread().ident
        
        with Logger._lock:
            current = Logger._thread_offsets.get(thread_id, 0)
            Logger._thread_offsets[thread_id] = current + INDENT
            
            # Only set global start_time if not already set
            if Logger.start_time is None:
                Logger.start_time = datetime.now()
                Logger.log("─" * 60)
    
    @staticmethod
    def end():
        """Mark end of operation and show elapsed time (thread-safe)"""
        thread_id = threading.current_thread().ident
        
        with Logger._lock:
            current = Logger._thread_offsets.get(thread_id, 0)
            Logger._thread_offsets[thread_id] = max(0, current - INDENT)
            
            # Only show elapsed time if start_time was set
            if Logger.start_time and Logger._thread_offsets[thread_id] == 0:
                elapsed = (datetime.now() - Logger.start_time).total_seconds()
                Logger.log(f"Completed in {elapsed:.2f}s")
                Logger.start_time = None
                Logger.log("─" * 60)
    
    @staticmethod
    def set_log_file(path: str):
        """Set log file path for persistent logging"""
        Logger.log_file = Path(path)
        Logger.log_file.parent.mkdir(parents=True, exist_ok=True)
    
    @staticmethod
    def disable_redaction():
        """
        Disable sensitive data redaction (for debugging only).
        
        WARNING: Use with caution! Sensitive credentials will be logged in plain text.
        """
        Logger.log("⚠️  Sensitive data redaction DISABLED - credentials will be logged!")
        Logger.SENSITIVE_PATTERNS = []