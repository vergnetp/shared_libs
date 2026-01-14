"""
app_kernel.utils - Common utilities.

Provides:
- Profiler: Function timing and performance analysis

Usage:
    from app_kernel.utils import Profiler, profiled_function
    
    # Simple timer
    profiler = Profiler()
    do_something()
    print(profiler.report("do_something"))
    
    # Decorator for function profiling
    @profiled_function(is_entry=True)
    def main():
        work()
    
    @profiled_function()
    def work():
        pass
"""

from .profiler import Profiler, profiled_function

__all__ = [
    "Profiler",
    "profiled_function",
]
