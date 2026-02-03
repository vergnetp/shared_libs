import functools
import asyncio
import inspect


def auto_transaction(func):
    """
    Decorator that automatically wraps a function in a transaction.
    Works for both sync and async functions.
    
    If the decorated function is called when a transaction is already in progress,
    it will use the existing transaction. Otherwise, it will create a new transaction,
    commit it if the function succeeds, or roll it back if an exception occurs.

    Need to be applied to methods of a class that offers in_transaction, begin_transaction (and commit/rollback)
    
    Concurrency safety:
        For async functions, uses self._tx_lock (asyncio.Lock) to serialize
        transaction boundaries. This prevents race conditions when multiple
        coroutines share a single connection (e.g. via asyncio.gather).
        
        The lock is ONLY acquired when starting a new transaction (in_transaction=False).
        Reentrant calls (where a transaction is already active) skip the lock entirely,
        so nested @auto_transaction (e.g. save_entity → execute) cannot deadlock.
    
    Usage:
        @auto_transaction
        def some_function(self, ...):
            # Function body, runs within a transaction
            
        @auto_transaction
        async def some_async_function(self, ...):
            # Async function body, runs within a transaction
    """
    is_async = asyncio.iscoroutinefunction(func)
    
    # Helper to safely call a method that might be sync or async
    async def _safely_await_if_needed(method, *args, **kwargs):
        if asyncio.iscoroutinefunction(method):
            return await method(*args, **kwargs)
        else:
            result = method(*args, **kwargs)
            if asyncio.iscoroutine(result):
                return await result
            return result
    
    @functools.wraps(func)
    def sync_wrapper(self, *args, **kwargs):
        # This wrapper is ONLY used for synchronous functions
        # Synchronous functions should only be used with classes
        # that have synchronous transaction methods
        if self.in_transaction():
            return func(self, *args, **kwargs)
        else:
            self.begin_transaction()
            try:
                result = func(self, *args, **kwargs)
                self.commit_transaction()
                return result
            except:
                self.rollback_transaction()
                raise

    @functools.wraps(func)
    async def async_wrapper(self, *args, **kwargs):
        # Fast path: already in a transaction (reentrant call from e.g. save_entity → execute).
        # No lock needed — the outer caller holds it.
        if await _safely_await_if_needed(self.in_transaction):
            return await func(self, *args, **kwargs)
        
        # Slow path: need to start a new transaction.
        # Serialize with _tx_lock to prevent concurrent BEGINs on shared connections.
        lock = getattr(self, '_tx_lock', None)
        if lock is None:
            # Fallback: create lock lazily (safe — no await between check and set)
            self._tx_lock = asyncio.Lock()
            lock = self._tx_lock
        
        async with lock:
            # Double-check after acquiring lock: another coroutine may have
            # started and committed a transaction while we waited.
            if await _safely_await_if_needed(self.in_transaction):
                return await func(self, *args, **kwargs)
            
            await _safely_await_if_needed(self.begin_transaction)
            try:
                result = await func(self, *args, **kwargs)
                await _safely_await_if_needed(self.commit_transaction)
                return result
            except:
                await _safely_await_if_needed(self.rollback_transaction)
                raise

    # Return appropriate wrapper based on whether the function is async or not
    if is_async:
        return async_wrapper
    else:
        return sync_wrapper