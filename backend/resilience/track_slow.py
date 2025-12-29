import functools
import asyncio
import time
import json

def track_slow_method(threshold_or_func=2.0):
    """
    Decorator that logs a warning if the execution of the method took longer than the threshold.
    Can be used as @track_slow_method or @track_slow_method(threshold=5.0)
    """
    from .. import log as logger
    
    def make_wrapper(func, threshold):
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                start = time.time()
                result = await func(*args, **kwargs)
                elapsed = time.time() - start

                if elapsed > threshold:
                    instance = args[0]
                    class_name = instance.__class__.__name__
                    method_name = func.__name__

                    try:
                        arg_str = json.dumps(args[1:], default=str)
                        kwarg_str = json.dumps(kwargs, default=str)
                    except Exception:
                        arg_str = str(args[1:])
                        kwarg_str = str(kwargs)

                    logger.warning(
                        f"Slow method {class_name}.{method_name} took {elapsed:.2f}s. "
                        f"Args={arg_str} Kwargs={kwarg_str}"
                    )

                return result
        else:
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                start = time.time()
                result = func(*args, **kwargs)
                elapsed = time.time() - start

                if elapsed > threshold:
                    instance = args[0]
                    class_name = instance.__class__.__name__
                    method_name = func.__name__

                    try:
                        arg_str = json.dumps(args[1:], default=str)
                        kwarg_str = json.dumps(kwargs, default=str)
                    except Exception:
                        arg_str = str(args[1:])
                        kwarg_str = str(kwargs)

                    logger.warning(
                        f"Slow method {class_name}.{method_name} took {elapsed:.2f}s. "
                        f"Args={arg_str} Kwargs={kwarg_str}"
                    )

                return result
        return wrapper
    
    # Handle both @track_slow_method and @track_slow_method(threshold=5.0)
    if callable(threshold_or_func):
        # Called without parentheses: @track_slow_method
        func = threshold_or_func
        return make_wrapper(func, 2.0)
    else:
        # Called with parentheses: @track_slow_method(threshold=5.0)
        threshold = threshold_or_func
        def decorator(func):
            return make_wrapper(func, threshold)
        return decorator