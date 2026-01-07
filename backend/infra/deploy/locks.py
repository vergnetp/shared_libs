"""
Deployment locking to prevent duplicate deployments.

Provides in-memory and optional persistent locking for deployments.

Service Identity:
    A service is uniquely identified by its container name:
    {workspace_id[:6]}_{project}_{env}_{service}
    
    This is the lock key.
"""

import time
import threading
from typing import Optional, Dict, Any
from dataclasses import dataclass, field


@dataclass
class DeploymentLock:
    """Represents an active deployment lock."""
    key: str  # Container name = service identity
    workspace_id: str
    project: str
    environment: str
    service: str
    started_at: float
    params: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def elapsed(self) -> float:
        """Seconds since lock was acquired."""
        return time.time() - self.started_at
    
    def is_expired(self, timeout: float = 300) -> bool:
        """Check if lock has expired (default 5 min timeout)."""
        return self.elapsed > timeout


class DeploymentLockManager:
    """
    Manages deployment locks to prevent duplicate deployments.
    
    Thread-safe in-memory implementation. For distributed systems,
    extend with Redis or database backend.
    
    Lock Key:
        Container name = {ws_short}_{project}_{env}_{service}
        This is the service identity.
    
    Usage:
        lock_mgr = DeploymentLockManager()
        
        # Try to acquire lock
        lock = lock_mgr.acquire(
            container_name="7f3a2b_hostomatic_prod_api",
            workspace_id="7f3a2b...",
            project="hostomatic",
            environment="prod",
            service="api"
        )
        if not lock:
            raise Exception("Deployment already in progress")
        
        try:
            # Do deployment...
            pass
        finally:
            lock_mgr.release(lock.key)
    """
    
    def __init__(self, lock_timeout: float = 300, cooldown: float = 30):
        """
        Args:
            lock_timeout: Max seconds a lock can be held (auto-expires after this)
            cooldown: Seconds after completion before same deployment can run again
        """
        self._locks: Dict[str, DeploymentLock] = {}
        self._completed: Dict[str, float] = {}  # key -> completion timestamp
        self._lock = threading.Lock()
        self.lock_timeout = lock_timeout
        self.cooldown = cooldown
    
    def acquire(
        self,
        container_name: str,
        workspace_id: str,
        project: str,
        environment: str,
        service: str,
        **params
    ) -> Optional[DeploymentLock]:
        """
        Try to acquire a deployment lock for a service.
        
        Args:
            container_name: The container name (service identity / lock key)
            workspace_id: Full workspace ID
            project: Project name
            environment: Environment (prod, dev, etc.)
            service: Service name
            **params: Additional params to store with lock
        
        Returns:
            DeploymentLock if acquired, None if deployment already in progress
        """
        key = container_name
        now = time.time()
        
        with self._lock:
            # Clean up expired locks
            self._cleanup_expired()
            
            # Check if already locked
            if key in self._locks:
                existing = self._locks[key]
                if not existing.is_expired(self.lock_timeout):
                    return None  # Already in progress
                # Expired, remove it
                del self._locks[key]
            
            # Check cooldown from previous completion
            if key in self._completed:
                completed_at = self._completed[key]
                if now - completed_at < self.cooldown:
                    return None  # Still in cooldown
            
            # Acquire lock
            lock = DeploymentLock(
                key=key,
                workspace_id=workspace_id,
                project=project,
                environment=environment,
                service=service,
                started_at=now,
                params=params
            )
            self._locks[key] = lock
            return lock
    
    def release(self, key: str, success: bool = True) -> None:
        """
        Release a deployment lock.
        
        Args:
            key: Lock key (container name) to release
            success: If True, starts cooldown period
        """
        with self._lock:
            if key in self._locks:
                del self._locks[key]
            
            if success:
                self._completed[key] = time.time()
    
    def force_release(self, container_name: str) -> bool:
        """
        Force release a lock (for cancellation or stuck deployments).
        
        Also clears any cooldown.
        
        Returns:
            True if lock was found and released, False otherwise
        """
        with self._lock:
            released = False
            if container_name in self._locks:
                del self._locks[container_name]
                released = True
            # Also clear cooldown
            if container_name in self._completed:
                del self._completed[container_name]
                released = True
            return released
    
    def is_locked(self, container_name: str) -> bool:
        """Check if a service deployment is currently locked."""
        with self._lock:
            self._cleanup_expired()
            
            if container_name in self._locks:
                return True
            
            # Also check cooldown
            if container_name in self._completed:
                if time.time() - self._completed[container_name] < self.cooldown:
                    return True
            
            return False
    
    def get_lock_info(self, container_name: str) -> Optional[Dict[str, Any]]:
        """Get info about an active lock."""
        with self._lock:
            if container_name in self._locks:
                lock = self._locks[container_name]
                return {
                    "locked": True,
                    "elapsed": lock.elapsed,
                    "workspace_id": lock.workspace_id,
                    "project": lock.project,
                    "environment": lock.environment,
                    "service": lock.service,
                    "params": lock.params,
                    "in_cooldown": False,
                }
            
            if container_name in self._completed:
                elapsed_since_complete = time.time() - self._completed[container_name]
                if elapsed_since_complete < self.cooldown:
                    return {
                        "locked": False,
                        "in_cooldown": True,
                        "cooldown_remaining": self.cooldown - elapsed_since_complete,
                    }
            
            return None
    
    def _cleanup_expired(self) -> None:
        """Remove expired locks and old completion records."""
        now = time.time()
        
        # Remove expired locks
        expired_keys = [
            k for k, v in self._locks.items()
            if v.is_expired(self.lock_timeout)
        ]
        for k in expired_keys:
            del self._locks[k]
        
        # Remove old completion records (keep for 1 hour max)
        old_keys = [
            k for k, t in self._completed.items()
            if now - t > 3600
        ]
        for k in old_keys:
            del self._completed[k]
    
    def clear_all(self) -> None:
        """Clear all locks (for testing)."""
        with self._lock:
            self._locks.clear()
            self._completed.clear()


# Global instance for simple usage
_default_manager: Optional[DeploymentLockManager] = None


def get_deployment_lock_manager() -> DeploymentLockManager:
    """Get the global deployment lock manager."""
    global _default_manager
    if _default_manager is None:
        _default_manager = DeploymentLockManager()
    return _default_manager
