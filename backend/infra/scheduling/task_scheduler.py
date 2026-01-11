"""
Task Scheduler - Cron-like scheduling for infrastructure tasks.

Supports scheduling:
- Health checks
- Auto-restart unhealthy containers
- Backups
- Custom tasks
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field
from enum import Enum
import json


logger = logging.getLogger(__name__)


class TaskType(str, Enum):
    HEALTH_CHECK = "health_check"
    AUTO_RESTART = "auto_restart"
    BACKUP = "backup"
    CUSTOM = "custom"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    DISABLED = "disabled"


@dataclass
class ScheduledTask:
    """A scheduled task definition."""
    id: str
    name: str
    task_type: TaskType
    interval_minutes: int  # Run every N minutes
    workspace_id: str
    enabled: bool = True
    
    # Task configuration
    config: Dict[str, Any] = field(default_factory=dict)
    
    # Runtime state
    last_run: Optional[datetime] = None
    last_status: TaskStatus = TaskStatus.PENDING
    last_result: Optional[str] = None
    run_count: int = 0
    error_count: int = 0
    
    def should_run(self) -> bool:
        """Check if task should run now."""
        if not self.enabled:
            return False
        if self.last_run is None:
            return True
        next_run = self.last_run + timedelta(minutes=self.interval_minutes)
        return datetime.utcnow() >= next_run
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "task_type": self.task_type.value,
            "interval_minutes": self.interval_minutes,
            "workspace_id": self.workspace_id,
            "enabled": self.enabled,
            "config": self.config,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "last_status": self.last_status.value,
            "last_result": self.last_result,
            "run_count": self.run_count,
            "error_count": self.error_count,
        }


class TaskScheduler:
    """
    In-memory task scheduler with async execution.
    
    Usage:
        scheduler = TaskScheduler()
        scheduler.register_handler(TaskType.HEALTH_CHECK, health_check_handler)
        scheduler.add_task(task)
        await scheduler.start()
    """
    
    def __init__(self, check_interval: int = 60):
        self.tasks: Dict[str, ScheduledTask] = {}
        self.handlers: Dict[TaskType, Callable] = {}
        self.check_interval = check_interval  # How often to check for due tasks
        self._running = False
        self._task: Optional[asyncio.Task] = None
    
    def register_handler(self, task_type: TaskType, handler: Callable):
        """Register a handler for a task type."""
        self.handlers[task_type] = handler
        logger.info(f"Registered handler for {task_type.value}")
    
    def add_task(self, task: ScheduledTask) -> ScheduledTask:
        """Add a scheduled task."""
        self.tasks[task.id] = task
        logger.info(f"Added task: {task.name} ({task.task_type.value}) every {task.interval_minutes}m")
        return task
    
    def remove_task(self, task_id: str) -> bool:
        """Remove a scheduled task."""
        if task_id in self.tasks:
            del self.tasks[task_id]
            logger.info(f"Removed task: {task_id}")
            return True
        return False
    
    def get_task(self, task_id: str) -> Optional[ScheduledTask]:
        """Get a task by ID."""
        return self.tasks.get(task_id)
    
    def list_tasks(self, workspace_id: Optional[str] = None) -> List[ScheduledTask]:
        """List all tasks, optionally filtered by workspace."""
        tasks = list(self.tasks.values())
        if workspace_id:
            tasks = [t for t in tasks if t.workspace_id == workspace_id]
        return tasks
    
    def enable_task(self, task_id: str) -> bool:
        """Enable a task."""
        if task_id in self.tasks:
            self.tasks[task_id].enabled = True
            return True
        return False
    
    def disable_task(self, task_id: str) -> bool:
        """Disable a task."""
        if task_id in self.tasks:
            self.tasks[task_id].enabled = False
            return True
        return False
    
    async def run_task(self, task: ScheduledTask) -> bool:
        """Execute a single task."""
        handler = self.handlers.get(task.task_type)
        if not handler:
            logger.warning(f"No handler for task type: {task.task_type}")
            return False
        
        task.last_status = TaskStatus.RUNNING
        task.last_run = datetime.utcnow()
        task.run_count += 1
        
        try:
            result = await handler(task)
            task.last_status = TaskStatus.SUCCESS
            task.last_result = str(result) if result else "OK"
            logger.info(f"Task {task.name} completed successfully")
            return True
        except Exception as e:
            task.last_status = TaskStatus.FAILED
            task.last_result = str(e)
            task.error_count += 1
            logger.error(f"Task {task.name} failed: {e}")
            return False
    
    async def _scheduler_loop(self):
        """Main scheduler loop."""
        logger.info(f"Scheduler started (check interval: {self.check_interval}s)")
        
        while self._running:
            try:
                # Find tasks that should run
                due_tasks = [t for t in self.tasks.values() if t.should_run()]
                
                if due_tasks:
                    logger.debug(f"Running {len(due_tasks)} due tasks")
                    # Run tasks concurrently
                    await asyncio.gather(*[self.run_task(t) for t in due_tasks], return_exceptions=True)
                
            except Exception as e:
                logger.error(f"Scheduler loop error: {e}")
            
            await asyncio.sleep(self.check_interval)
        
        logger.info("Scheduler stopped")
    
    async def start(self):
        """Start the scheduler."""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())
    
    async def stop(self):
        """Stop the scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
    
    def get_status(self) -> Dict[str, Any]:
        """Get scheduler status."""
        return {
            "running": self._running,
            "task_count": len(self.tasks),
            "enabled_count": sum(1 for t in self.tasks.values() if t.enabled),
            "handlers": list(self.handlers.keys()),
        }


# Global scheduler instance
_scheduler: Optional[TaskScheduler] = None


def get_scheduler() -> TaskScheduler:
    """Get or create the global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = TaskScheduler()
    return _scheduler
