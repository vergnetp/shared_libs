"""Scheduling - Task scheduling for infrastructure operations.

Note: Legacy Scheduler and BackupManager classes have been removed.
Use TaskScheduler with handlers for scheduling tasks.
"""

from .task_scheduler import (
    TaskScheduler,
    ScheduledTask,
    TaskType,
    TaskStatus,
    get_scheduler,
)
from .handlers import (
    health_check_handler,
    auto_restart_handler,
    backup_handler,
    register_all_handlers,
    TASK_HANDLERS,
)

__all__ = [
    # Centralized task scheduler
    "TaskScheduler",
    "ScheduledTask",
    "TaskType",
    "TaskStatus",
    "get_scheduler",
    # Handlers
    "register_all_handlers",
    "TASK_HANDLERS",
    "health_check_handler",
    "auto_restart_handler",
    "backup_handler",
]
