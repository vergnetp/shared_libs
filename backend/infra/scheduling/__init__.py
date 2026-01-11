"""Scheduling - Cron jobs, backups, scheduled tasks."""

from .scheduler import (
    Scheduler,
    CronJob,
    ScheduledTask,
    ScheduleFrequency,
)
from .backup import (
    BackupManager,
    BackupConfig,
    BackupResult,
    BackupType,
    StorageType,
)
from .task_scheduler import (
    TaskScheduler,
    ScheduledTask as CentralizedTask,
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
    # Server-side cron
    "Scheduler",
    "CronJob",
    "ScheduledTask",
    "ScheduleFrequency",
    # Backup
    "BackupManager",
    "BackupConfig",
    "BackupResult",
    "BackupType",
    "StorageType",
    # Centralized task scheduler
    "TaskScheduler",
    "CentralizedTask",
    "TaskType",
    "TaskStatus",
    "get_scheduler",
    "register_all_handlers",
    "TASK_HANDLERS",
    "health_check_handler",
    "auto_restart_handler",
    "backup_handler",
]
