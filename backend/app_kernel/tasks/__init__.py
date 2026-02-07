"""
app_kernel.tasks - Cancellable SSE-streamed tasks.

Provides TaskStream for long-running operations that stream progress
via Server-Sent Events and support user-initiated cancellation.

Quick start:
    from app_kernel.tasks import TaskStream, TaskCancelled

    async def deploy(db, ...) -> AsyncIterator[str]:
        stream = TaskStream("deploy")
        try:
            yield stream.task_id_event()
            
            stream("Step 1...")
            yield stream.log()
            await do_work()
            stream.check()
            
            stream("Step 2...")
            yield stream.log()
            
            yield stream.complete(True)
        except TaskCancelled:
            yield stream.complete(False, error='Cancelled by user')
        finally:
            stream.cleanup()

Cancel endpoint:
    from app_kernel.tasks import create_tasks_router
    app.include_router(create_tasks_router(auth_dependency=get_current_user))

Client-side:
    1. Listen for `event: task_id` in SSE stream
    2. POST /tasks/{task_id}/cancel to cancel
"""

# Cancel registry
from .cancel import (
    TaskCancelled,
    Cancelled,  # Backwards compat alias
    register,
    trigger,
    cleanup,
    check,
    is_active,
    is_cancelled,
)

# SSE formatters
from .sse import (
    sse_event,
    sse_task_id,
    sse_log,
    sse_complete,
    sse_urls,
)

# TaskStream
from .stream import TaskStream

# Router
from .router import create_tasks_router

__all__ = [
    # Exception
    "TaskCancelled",
    "Cancelled",  # Backwards compat,
    
    # Cancel registry
    "register",
    "trigger",
    "cleanup",
    "check",
    "is_active",
    "is_cancelled",
    
    # SSE
    "sse_event",
    "sse_task_id",
    "sse_log",
    "sse_complete",
    "sse_urls",
    
    # TaskStream
    "TaskStream",
    
    # Router
    "create_tasks_router",
]