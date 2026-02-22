import functools
import asyncio
import time
import random
import sqlite3, psycopg2, asyncpg, pymysql, aiomysql, aiosqlite

def retry_with_backoff(max_retries=3, base_delay=0.1, max_delay=10.0, 
                      exceptions=None, total_timeout=30.0,
                      retry_on_timeout=False):
    """
    Decorator for retrying functions with exponential backoff on specified exceptions.
    
    Args:
        max_retries (int): Maximum number of retry attempts
        base_delay (float): Initial delay in seconds
        max_delay (float): Maximum delay between retries in seconds
        exceptions (tuple, optional): Exception types to catch and retry. If None,
                                     defaults to common database exceptions.
        total_timeout (float, optional): Maximum total time for all retries in seconds.
                                        Default is 30.0 seconds. Set to None to disable.
        retry_on_timeout (bool): If True, also retry on TimeoutError and
                                 asyncio.CancelledError (from outer wait_for).
                                 Use when retry wraps an operation that has its own
                                 inner timeout. Default False.
    """
    # Default common database exceptions to catch
    if exceptions is None:
        exceptions = (
            # Generic exception types that work across drivers
            ConnectionError,
            TimeoutError,
            # Combined list of common errors from various DB drivers
            # These are string names to avoid import errors if a driver isn't installed
            'OperationalError',
            'InterfaceError',
            'InternalError',
            'PoolError',
            'DatabaseError'
        )
    
    # Convert string exception names to actual exception classes if available
    exception_classes = []
    for exc in exceptions:
        if isinstance(exc, str):
            # Look for exception in common database modules
            for module in [sqlite3, psycopg2, asyncpg, pymysql, aiomysql, aiosqlite]:
                if hasattr(module, exc):
                    exception_classes.append(getattr(module, exc))
        else:
            exception_classes.append(exc)
    
    if exception_classes:
        exceptions = tuple(exception_classes)
    
    def decorator(func):

        # Add retry information to docstring
        msg = ''
        msg += f"\n\n        Note: Automatically retries on failure up to {max_retries} times"
        msg += f"\n        Retry delay: {base_delay}s initial, {max_delay}s maximum"
        if total_timeout:
            msg += f"\n        Total retry timeout: {total_timeout}s"
        func.__doc__ = '' if not func.__doc__ else func.__doc__
        func.__doc__ += msg
        
        from .. import log as logger
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            retries = 0
            delay = base_delay
            start_time = time.time()
            last_exception = None
            
            while True:
                # Check total timeout if set
                if total_timeout is not None and time.time() - start_time > total_timeout:
                    method_name = getattr(args[0].__class__ if args else None, '__name__', 'unknown') + '.' + func.__name__
                    elapsed = time.time() - start_time
                    logger.warning(
                        f"Total timeout of {total_timeout}s exceeded for {method_name} "
                        f"(elapsed={elapsed:.1f}s, retries={retries}/{max_retries})"
                    )
                    if last_exception:
                        raise last_exception
                    raise TimeoutError(f"Operation timed out after {total_timeout}s for {method_name}")
                
                try:
                    return await func(*args, **kwargs)
                except asyncio.CancelledError:
                    # CancelledError during the operation itself (not during sleep).
                    #
                    # Common cause: an outer asyncio.wait_for timed out and cancelled us.
                    # If retry_on_timeout is True, treat this as retryable (the outer
                    # timeout killed our attempt, but we can try again).
                    # Otherwise, propagate — the caller explicitly cancelled.
                    if retry_on_timeout:
                        elapsed = time.time() - start_time
                        last_exception = TimeoutError(
                            f"Operation cancelled (likely outer timeout) for {func.__name__} "
                            f"after {elapsed:.1f}s"
                        )
                        retries += 1
                        if retries > max_retries:
                            logger.warning(
                                f"Max retries ({max_retries}) exceeded for {func.__name__} "
                                f"(cancelled/timeout, elapsed={elapsed:.1f}s)"
                            )
                            raise last_exception

                        jitter = random.uniform(0.8, 1.2)
                        sleep_time = min(delay * jitter, max_delay)

                        logger.info(
                            f"Retry {retries}/{max_retries} for {func.__name__} after "
                            f"cancellation, sleeping {sleep_time:.1f}s"
                        )
                        # This sleep is safe — we're between attempts, not inside
                        # any inner wait_for.  But if the WHOLE task is cancelled
                        # (app shutdown etc), we still honour that.
                        await asyncio.sleep(sleep_time)
                        delay = min(delay * 2, max_delay)
                        continue
                    else:
                        raise
                except exceptions as e:
                    last_exception = e
                    retries += 1
                    elapsed = time.time() - start_time
                    if retries > max_retries:
                        logger.warning(
                            f"Max retries ({max_retries}) exceeded for {func.__name__}: {e} "
                            f"(elapsed={elapsed:.1f}s)"
                        )
                        raise last_exception
                    
                    # Calculate delay with jitter to avoid thundering herd
                    jitter = random.uniform(0.8, 1.2)
                    sleep_time = min(delay * jitter, max_delay)
                    
                    # Check if next sleep would exceed total timeout
                    if total_timeout is not None:
                        remaining = total_timeout - elapsed
                        if remaining <= sleep_time:
                            # If we can't do a full sleep, either do a shorter one or just timeout now
                            if remaining > 0.1:  # Only sleep if we have a meaningful amount of time left
                                sleep_time = remaining * 0.9  # Leave a little margin
                                logger.debug(f"Adjusting sleep time to {sleep_time:.2f}s to respect total timeout")
                            else:
                                logger.warning(
                                    f"Total timeout of {total_timeout}s about to exceed for "
                                    f"{func.__name__} (elapsed={elapsed:.1f}s, retries={retries})"
                                )
                                raise last_exception
                    
                    logger.debug(
                        f"Retry {retries}/{max_retries} for {func.__name__} after "
                        f"{sleep_time:.2f}s (elapsed={elapsed:.1f}s): {str(e)[:100]}"
                    )
                    try:
                        await asyncio.sleep(sleep_time)
                    except asyncio.CancelledError:
                        # Sleep was killed — most likely an outer asyncio.wait_for
                        # timed out and cancelled us mid-backoff.
                        sleep_elapsed = time.time() - start_time
                        logger.warning(
                            f"Retry sleep cancelled for {func.__name__} "
                            f"(retry {retries}/{max_retries}, total_elapsed={sleep_elapsed:.1f}s). "
                            f"Likely an outer timeout killed the backoff. "
                            f"Consider moving retry OUTSIDE the timeout wrapper."
                        )
                        raise last_exception
                    
                    # Exponential backoff
                    delay = min(delay * 2, max_delay)
        
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            retries = 0
            delay = base_delay
            start_time = time.time()
            last_exception = None
            
            while True:
                # Check total timeout if set
                if total_timeout is not None and time.time() - start_time > total_timeout:
                    method_name = getattr(args[0].__class__ if args else None, '__name__', 'unknown') + '.' + func.__name__
                    elapsed = time.time() - start_time
                    logger.warning(
                        f"Total timeout of {total_timeout}s exceeded for {method_name} "
                        f"(elapsed={elapsed:.1f}s, retries={retries}/{max_retries})"
                    )
                    if last_exception:
                        raise last_exception
                    raise TimeoutError(f"Operation timed out after {total_timeout}s for {method_name}")
                
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    retries += 1
                    elapsed = time.time() - start_time
                    if retries > max_retries:
                        logger.warning(
                            f"Max retries ({max_retries}) exceeded for {func.__name__}: {e} "
                            f"(elapsed={elapsed:.1f}s)"
                        )
                        raise last_exception
                    
                    # Calculate delay with jitter to avoid thundering herd
                    jitter = random.uniform(0.8, 1.2)
                    sleep_time = min(delay * jitter, max_delay)
                    
                    # Check if next sleep would exceed total timeout
                    if total_timeout is not None:
                        remaining = total_timeout - elapsed
                        if remaining <= sleep_time:
                            # If we can't do a full sleep, either do a shorter one or just timeout now
                            if remaining > 0.1:  # Only sleep if we have a meaningful amount of time left
                                sleep_time = remaining * 0.9  # Leave a little margin
                                logger.debug(f"Adjusting sleep time to {sleep_time:.2f}s to respect total timeout")
                            else:
                                logger.warning(
                                    f"Total timeout of {total_timeout}s about to exceed for "
                                    f"{func.__name__} (elapsed={elapsed:.1f}s, retries={retries})"
                                )
                                raise last_exception
                    
                    logger.debug(
                        f"Retry {retries}/{max_retries} for {func.__name__} after "
                        f"{sleep_time:.2f}s (elapsed={elapsed:.1f}s): {str(e)[:100]}"
                    )
                    time.sleep(sleep_time)
                    
                    # Exponential backoff
                    delay = min(delay * 2, max_delay)
        
        # Return appropriate wrapper based on whether the function is async or not
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator