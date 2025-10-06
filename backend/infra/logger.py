# logger.py - Add threading support

import os
from datetime import datetime
from pathlib import Path
import threading

INDENT = 2

class Logger:
    offset = 0
    log_file = None
    _lock = threading.Lock()  # NEW: Thread lock
    _thread_offsets = {}  # NEW: Per-thread indentation
    
    @staticmethod
    def _get_log_file():
        with Logger._lock:  # ADD LOCK HERE
            if Logger.log_file is None:
                log_dir = Path("C:\\logs")
                log_dir.mkdir(exist_ok=True)
                
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                Logger.log_file = log_dir / f"deployment_{timestamp}.log"
                
                with open(Logger.log_file, 'w', encoding='utf-8') as f:
                    f.write(f"=== Deployment Log Started at {datetime.now().isoformat()} ===\n")
            
            return Logger.log_file
    
    @staticmethod
    def start():
        thread_id = threading.current_thread().ident
        with Logger._lock:
            current = Logger._thread_offsets.get(thread_id, 0)
            Logger._thread_offsets[thread_id] = current + INDENT
    
    @staticmethod
    def end():
        thread_id = threading.current_thread().ident
        with Logger._lock:
            current = Logger._thread_offsets.get(thread_id, 0)
            Logger._thread_offsets[thread_id] = max(0, current - INDENT)
    
    @staticmethod
    def log(msg):
        thread_id = threading.current_thread().ident
        thread_name = threading.current_thread().name
        
        with Logger._lock:
            offset = Logger._thread_offsets.get(thread_id, 0)
            
            # Include thread name for parallel operations
            if thread_name.startswith("ThreadPoolExecutor"):
                prefix = f"[Thread-{thread_id % 1000}] "
            else:
                prefix = ""
            
            indented_msg = f"{' ' * offset}{prefix}{msg}"
            
            # Print to console
            print(indented_msg)
            
            # Write to file (thread-safe)
            try:
                log_file = Logger._get_log_file()
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(f"[{timestamp}] {indented_msg}\n")
                    f.flush()
            except Exception as e:
                print(f"LOG FILE ERROR: {e}")