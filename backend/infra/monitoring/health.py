"""
Health Monitoring - Container and service health checks.

Supports:
- HTTP health checks
- TCP port checks  
- Docker health status
- Custom exec checks
- Log-based error detection
- Scheduled task validation
"""

from __future__ import annotations
import asyncio
import re
import socket
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, Any, List, Optional, Callable, Tuple
from enum import Enum
from datetime import datetime, timezone

# urllib is used directly for internal health checks (not http_client) because:
# - Health checks should fail fast (no retry/backoff)
# - One container down shouldn't trip circuit breakers for others
# - Internal localhost checks shouldn't pollute tracing/observability
# - Connection pooling is unnecessary for simple health probes
import urllib.request
import urllib.error

if TYPE_CHECKING:
    from ..context import DeploymentContext
    from ..core.service import Service, ServiceHealthCheck

# Import NodeAgentClient for remote health checks (avoids direct TCP/SSH)
from ..node_agent.client import NodeAgentClient


# =============================================================================
# Error Detection Patterns
# =============================================================================

# Patterns that indicate errors in logs
ERROR_PATTERNS = [
    r"Traceback \(most recent call last\)",  # Python exceptions
    r"^ERROR[:\s]",                           # ERROR: or ERROR 
    r"\bERROR\b.*:",                          # ERROR in context
    r"^FATAL[:\s]",                           # FATAL: or FATAL
    r"\bFATAL\b.*:",                          # FATAL in context  
    r"^CRITICAL[:\s]",                        # CRITICAL: or CRITICAL
    r"\bCRITICAL\b.*:",                       # CRITICAL in context
    r"panic:",                                # Go panics
    r"SIGKILL",                               # Process killed
    r"OOMKilled",                             # Out of memory
    r"Killed",                                # Process terminated
    r"Segmentation fault",                    # Segfault
    r"core dumped",                           # Core dump
    r"Exception:",                            # Generic exception
    r"failed to start",                       # Startup failure
    r"connection refused",                    # Network issue
    r"permission denied",                     # Permission issue
]

# Compiled patterns for performance
_ERROR_REGEX = re.compile("|".join(ERROR_PATTERNS), re.IGNORECASE | re.MULTILINE)


class HealthStatus(Enum):
    """Health check status."""
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


@dataclass
class HealthCheckResult:
    """Result of a health check."""
    status: HealthStatus
    message: str = ""
    response_time_ms: Optional[float] = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    details: Dict[str, Any] = field(default_factory=dict)
    error_lines: List[str] = field(default_factory=list)  # Log lines with errors
    
    @property
    def is_healthy(self) -> bool:
        return self.status == HealthStatus.HEALTHY
    
    @property 
    def is_degraded(self) -> bool:
        return self.status == HealthStatus.DEGRADED
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "message": self.message,
            "response_time_ms": self.response_time_ms,
            "checked_at": self.checked_at.isoformat(),
            "details": self.details,
            "error_lines": self.error_lines[:10] if self.error_lines else [],  # Limit to 10
        }


@dataclass
class ServiceHealth:
    """Aggregated health for a service."""
    service_name: str
    status: HealthStatus
    checks: List[HealthCheckResult] = field(default_factory=list)
    containers_healthy: int = 0
    containers_total: int = 0
    last_check: Optional[datetime] = None
    
    @property
    def health_percentage(self) -> float:
        if self.containers_total == 0:
            return 0.0
        return (self.containers_healthy / self.containers_total) * 100
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "service_name": self.service_name,
            "status": self.status.value,
            "health_percentage": self.health_percentage,
            "containers_healthy": self.containers_healthy,
            "containers_total": self.containers_total,
            "last_check": self.last_check.isoformat() if self.last_check else None,
            "checks": [c.to_dict() for c in self.checks],
        }


class HealthChecker:
    """
    Health checker for services.
    
    Usage:
        checker = HealthChecker(ctx)
        
        # Check single endpoint
        result = await checker.check_http("http://localhost:8000/health")
        
        # Check TCP port
        result = await checker.check_tcp("localhost", 5432)
        
        # Check service
        health = await checker.check_service(service, containers)
    """
    
    def __init__(
        self, 
        ctx: 'DeploymentContext' = None,
        do_token: Optional[str] = None,
    ):
        self.ctx = ctx
        self.do_token = do_token
        self._agent_clients: Dict[str, NodeAgentClient] = {}
    
    def _get_agent_client(self, server_ip: str) -> Optional[NodeAgentClient]:
        """
        Get or create NodeAgentClient for a server.
        
        Returns None if no do_token configured.
        """
        if not self.do_token:
            return None
        
        if server_ip not in self._agent_clients:
            self._agent_clients[server_ip] = NodeAgentClient(
                server_ip=server_ip,
                do_token=self.do_token,
            )
        return self._agent_clients[server_ip]
    
    def _is_remote(self, host: str) -> bool:
        """Check if host is remote (not localhost)."""
        return host not in ("localhost", "127.0.0.1", "::1")
    
    # =========================================================================
    # Individual Checks
    # =========================================================================
    
    async def check_http(
        self,
        url: str,
        method: str = "GET",
        timeout: int = 10,
        expected_status: int = 200,
        headers: Optional[Dict[str, str]] = None,
    ) -> HealthCheckResult:
        """
        HTTP health check.
        
        Args:
            url: URL to check
            method: HTTP method
            timeout: Timeout in seconds
            expected_status: Expected HTTP status code
            headers: Optional headers
            
        Returns:
            HealthCheckResult
        """
        start = time.time()
        
        try:
            req = urllib.request.Request(url, method=method)
            
            if headers:
                for key, value in headers.items():
                    req.add_header(key, value)
            
            response = await asyncio.to_thread(
                urllib.request.urlopen,
                req,
                timeout=timeout,
            )
            
            elapsed = (time.time() - start) * 1000
            status_code = response.getcode()
            
            if status_code == expected_status:
                return HealthCheckResult(
                    status=HealthStatus.HEALTHY,
                    message=f"HTTP {status_code}",
                    response_time_ms=elapsed,
                    details={"url": url, "status_code": status_code},
                )
            else:
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message=f"Unexpected status: {status_code}",
                    response_time_ms=elapsed,
                    details={"url": url, "status_code": status_code, "expected": expected_status},
                )
                
        except urllib.error.HTTPError as e:
            elapsed = (time.time() - start) * 1000
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"HTTP error: {e.code}",
                response_time_ms=elapsed,
                details={"url": url, "error": str(e)},
            )
        except urllib.error.URLError as e:
            elapsed = (time.time() - start) * 1000
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"Connection failed: {e.reason}",
                response_time_ms=elapsed,
                details={"url": url, "error": str(e)},
            )
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"Check failed: {e}",
                response_time_ms=elapsed,
                details={"url": url, "error": str(e)},
            )
    
    async def check_tcp(
        self,
        host: str,
        port: int,
        timeout: int = 5,
    ) -> HealthCheckResult:
        """
        TCP port health check.
        
        Args:
            host: Host to check
            port: Port to check
            timeout: Timeout in seconds
            
        Returns:
            HealthCheckResult
        """
        start = time.time()
        
        def _check():
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            try:
                result = sock.connect_ex((host, port))
                return result == 0
            finally:
                sock.close()
        
        try:
            success = await asyncio.to_thread(_check)
            elapsed = (time.time() - start) * 1000
            
            if success:
                return HealthCheckResult(
                    status=HealthStatus.HEALTHY,
                    message=f"Port {port} is open",
                    response_time_ms=elapsed,
                    details={"host": host, "port": port},
                )
            else:
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message=f"Port {port} is closed",
                    response_time_ms=elapsed,
                    details={"host": host, "port": port},
                )
                
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"TCP check failed: {e}",
                response_time_ms=elapsed,
                details={"host": host, "port": port, "error": str(e)},
            )
    
    # =========================================================================
    # Remote Checks via Node Agent (no direct TCP/SSH needed)
    # =========================================================================
    
    async def check_docker_via_agent(
        self,
        server_ip: str,
        container_name: str,
        check_logs: bool = True,
    ) -> HealthCheckResult:
        """
        Comprehensive container health check via node agent API.
        
        Uses the unified /containers/{name}/health endpoint which:
        - Checks container running state
        - Auto-discovers exposed port and verifies TCP connectivity
        - Analyzes recent logs for errors
        
        Args:
            server_ip: Server IP address
            container_name: Container name
            check_logs: Whether to include log analysis (default True)
            
        Returns:
            HealthCheckResult with comprehensive status
        """
        client = self._get_agent_client(server_ip)
        if not client:
            return HealthCheckResult(
                status=HealthStatus.UNKNOWN,
                message="No DO token configured for remote health checks",
                details={"server": server_ip, "container": container_name},
            )
        
        start = time.time()
        try:
            # Use unified health check endpoint
            result = await client.check_container_health(container_name)
            elapsed = (time.time() - start) * 1000
            
            if not result.success:
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message=f"Health check failed: {result.error}",
                    response_time_ms=elapsed,
                    details={"server": server_ip, "container": container_name, "via": "node_agent"},
                )
            
            data = result.data
            status_str = data.get("status", "unknown")
            details_msg = data.get("details", "")
            
            # Map agent status to HealthStatus
            if status_str == "healthy":
                status = HealthStatus.HEALTHY
            elif status_str == "degraded":
                status = HealthStatus.DEGRADED
            else:
                status = HealthStatus.UNHEALTHY
            
            # Build comprehensive details
            check_details = {
                "server": server_ip,
                "container": container_name,
                "via": "node_agent_unified",
            }
            
            # Add container info if available
            container_info = data.get("container", {})
            if container_info:
                check_details["running"] = container_info.get("running", False)
                check_details["docker_health"] = container_info.get("health_status")
                if container_info.get("discovered_port"):
                    check_details["discovered_port"] = container_info["discovered_port"]
            
            # Add port check result if available
            port_check = data.get("port_check", {})
            if port_check:
                check_details["port_check"] = port_check.get("status")
                if port_check.get("response_time_ms"):
                    check_details["port_response_ms"] = port_check["response_time_ms"]
            
            # Add error lines if degraded
            error_lines = None
            logs_info = data.get("logs", {})
            if logs_info and logs_info.get("has_errors"):
                error_lines = logs_info.get("error_lines", [])
            
            return HealthCheckResult(
                status=status,
                message=details_msg or f"Container {status_str}",
                response_time_ms=elapsed,
                details=check_details,
                error_lines=error_lines,
            )
                
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return HealthCheckResult(
                status=HealthStatus.UNKNOWN,
                message=f"Agent check failed: {e}",
                response_time_ms=elapsed,
                details={"server": server_ip, "container": container_name, "error": str(e)},
            )
    
    # =========================================================================
    # Log-Based Error Detection
    # =========================================================================
    
    async def check_logs_for_errors(
        self,
        server_ip: str,
        container_name: str,
        since: Optional[str] = "5m",
        lines: int = 200,
    ) -> Tuple[bool, List[str]]:
        """
        Scan container logs for error patterns.
        
        Args:
            server_ip: Server IP address
            container_name: Container name
            since: Time window ("5m", "1h", or ISO timestamp)
            lines: Maximum lines to scan
            
        Returns:
            Tuple of (has_errors: bool, error_lines: List[str])
        """
        client = self._get_agent_client(server_ip)
        if not client:
            return False, []  # Can't check without agent
        
        try:
            result = await client.container_logs(container_name, lines=lines, since=since)
            
            if not result.success:
                return False, []  # No logs or container not found
            
            logs = result.data.get("logs", "") if result.data else ""
            if not logs:
                return False, []
            
            # Find error lines
            error_lines = []
            for line in logs.split("\n"):
                if _ERROR_REGEX.search(line):
                    # Truncate long lines
                    error_lines.append(line[:500] if len(line) > 500 else line)
                    if len(error_lines) >= 20:  # Cap at 20 error lines
                        break
            
            return len(error_lines) > 0, error_lines
            
        except Exception:
            return False, []  # Don't fail health check on log errors
    
    async def check_scheduled_task(
        self,
        server_ip: str,
        container_name: str,
        schedule: str,
    ) -> HealthCheckResult:
        """
        Check health of a scheduled (cron) task.
        
        For scheduled tasks, we check:
        1. Container exists
        2. Last run exit code was 0
        3. Last run finished recently (within expected interval + buffer)
        4. No errors in recent logs
        
        Args:
            server_ip: Server IP address
            container_name: Container name
            schedule: Cron schedule string (e.g., "0 * * * *" for hourly)
            
        Returns:
            HealthCheckResult
        """
        client = self._get_agent_client(server_ip)
        if not client:
            return HealthCheckResult(
                status=HealthStatus.UNKNOWN,
                message="No DO token configured for scheduled task checks",
                details={"server": server_ip, "container": container_name},
            )
        
        start = time.time()
        try:
            result = await client.check_container_health(container_name)
            elapsed = (time.time() - start) * 1000
            
            if not result.success:
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message=f"Container not found: {result.error}",
                    response_time_ms=elapsed,
                    details={"server": server_ip, "container": container_name},
                )
            
            data = result.data or {}
            # New format: container info nested under 'container' key
            container_info = data.get("container", {})
            running = container_info.get("running", False)
            exit_code = container_info.get("exit_code")
            finished_at = container_info.get("finished_at", "")
            oom_killed = container_info.get("oom_killed", False)
            status = container_info.get("status", "unknown")
            
            details = {
                "server": server_ip,
                "container": container_name,
                "schedule": schedule,
                "exit_code": exit_code,
                "finished_at": finished_at,
                "oom_killed": oom_killed,
                "status": status,
                "via": "node_agent",
            }
            
            # Check for OOM kill
            if oom_killed:
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message="Task was killed due to out of memory",
                    response_time_ms=elapsed,
                    details=details,
                )
            
            # If currently running, that's healthy
            if running:
                return HealthCheckResult(
                    status=HealthStatus.HEALTHY,
                    message="Scheduled task currently running",
                    response_time_ms=elapsed,
                    details=details,
                )
            
            # Check exit code of last run
            if exit_code is not None and exit_code != 0:
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message=f"Last run failed with exit code {exit_code}",
                    response_time_ms=elapsed,
                    details=details,
                )
            
            # Check if finished recently (parse cron to estimate expected interval)
            expected_interval = self._parse_cron_interval(schedule)
            if finished_at and expected_interval:
                try:
                    # Parse finished_at (Docker format: 2024-01-15T10:30:00.123456Z)
                    finished_dt = datetime.fromisoformat(
                        finished_at.replace("Z", "+00:00").split(".")[0] + "+00:00"
                    )
                    now = datetime.now(timezone.utc)
                    age_seconds = (now - finished_dt).total_seconds()
                    
                    # Allow 2x interval + 5 min buffer
                    max_age = (expected_interval * 2) + 300
                    details["last_run_age_seconds"] = int(age_seconds)
                    details["expected_interval_seconds"] = expected_interval
                    
                    if age_seconds > max_age:
                        return HealthCheckResult(
                            status=HealthStatus.UNHEALTHY,
                            message=f"Scheduled task hasn't run in {int(age_seconds/60)} minutes",
                            response_time_ms=elapsed,
                            details=details,
                        )
                except (ValueError, TypeError):
                    pass  # Can't parse timestamp, skip age check
            
            # Check for errors in recent logs
            has_errors, error_lines = await self.check_logs_for_errors(
                server_ip, container_name, since="1h"
            )
            
            if has_errors:
                return HealthCheckResult(
                    status=HealthStatus.DEGRADED,
                    message="Last run succeeded but errors found in logs",
                    response_time_ms=elapsed,
                    details=details,
                    error_lines=error_lines,
                )
            
            # All checks passed
            return HealthCheckResult(
                status=HealthStatus.HEALTHY,
                message="Scheduled task healthy",
                response_time_ms=elapsed,
                details=details,
            )
            
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return HealthCheckResult(
                status=HealthStatus.UNKNOWN,
                message=f"Failed to check scheduled task: {e}",
                response_time_ms=elapsed,
                details={"server": server_ip, "container": container_name, "error": str(e)},
            )
    
    def _parse_cron_interval(self, schedule: str) -> Optional[int]:
        """
        Parse cron schedule and return expected interval in seconds.
        
        Supports standard cron format: minute hour day month weekday
        Examples:
            "* * * * *" -> 60 (every minute)
            "0 * * * *" -> 3600 (every hour)
            "0 0 * * *" -> 86400 (every day)
            "*/5 * * * *" -> 300 (every 5 minutes)
            
        Returns None if can't parse.
        """
        try:
            parts = schedule.strip().split()
            if len(parts) != 5:
                return None
            
            minute, hour, day, month, weekday = parts
            
            # Every minute
            if minute == "*":
                return 60
            
            # Every N minutes
            if minute.startswith("*/"):
                n = int(minute[2:])
                return n * 60
            
            # Specific minute(s), check hour
            if hour == "*":
                return 3600  # Every hour
            
            if hour.startswith("*/"):
                n = int(hour[2:])
                return n * 3600
            
            # Specific hour, check day
            if day == "*":
                return 86400  # Every day
            
            # Weekly or monthly - just estimate as daily
            return 86400
            
        except Exception:
            return None
    
    async def check_worker_health(
        self,
        server_ip: str,
        container_name: str,
        check_logs: bool = True,
    ) -> HealthCheckResult:
        """
        Check health of a worker service (no HTTP port).
        
        For workers, we check:
        1. Container is running
        2. No OOM kill
        3. No errors in recent logs (if check_logs=True)
        
        Args:
            server_ip: Server IP address
            container_name: Container name
            check_logs: Whether to scan logs for errors
            
        Returns:
            HealthCheckResult
        """
        client = self._get_agent_client(server_ip)
        if not client:
            return HealthCheckResult(
                status=HealthStatus.UNKNOWN,
                message="No DO token configured for worker health checks",
                details={"server": server_ip, "container": container_name},
            )
        
        start = time.time()
        try:
            result = await client.check_container_health(container_name)
            elapsed = (time.time() - start) * 1000
            
            if not result.success:
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message=f"Container not found: {result.error}",
                    response_time_ms=elapsed,
                    details={"server": server_ip, "container": container_name},
                )
            
            data = result.data or {}
            # New format nests container info under 'container' key
            container_data = data.get("container", data)  # Fallback to flat for compatibility
            running = container_data.get("running", False)
            health_status = container_data.get("health", "none")
            oom_killed = container_data.get("oom_killed", False)
            restart_count = container_data.get("restart_count", 0)
            
            details = {
                "server": server_ip,
                "container": container_name,
                "running": running,
                "health": health_status,
                "oom_killed": oom_killed,
                "restart_count": restart_count,
                "via": "node_agent",
            }
            
            # Not running = unhealthy
            if not running:
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message="Worker not running",
                    response_time_ms=elapsed,
                    details=details,
                )
            
            # OOM killed = unhealthy
            if oom_killed:
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message="Worker was killed due to out of memory",
                    response_time_ms=elapsed,
                    details=details,
                )
            
            # Docker health check says unhealthy
            if health_status == "unhealthy":
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message="Docker health check reports unhealthy",
                    response_time_ms=elapsed,
                    details=details,
                )
            
            # Check logs for errors (degrades to DEGRADED, not UNHEALTHY)
            error_lines = []
            if check_logs:
                has_errors, error_lines = await self.check_logs_for_errors(
                    server_ip, container_name, since="5m"
                )
                
                if has_errors:
                    return HealthCheckResult(
                        status=HealthStatus.DEGRADED,
                        message="Worker running but errors found in logs",
                        response_time_ms=elapsed,
                        details=details,
                        error_lines=error_lines,
                    )
            
            # High restart count = degraded
            if restart_count > 5:
                return HealthCheckResult(
                    status=HealthStatus.DEGRADED,
                    message=f"Worker has restarted {restart_count} times",
                    response_time_ms=elapsed,
                    details=details,
                )
            
            # All good
            return HealthCheckResult(
                status=HealthStatus.HEALTHY,
                message="Worker healthy",
                response_time_ms=elapsed,
                details=details,
            )
            
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return HealthCheckResult(
                status=HealthStatus.UNKNOWN,
                message=f"Failed to check worker health: {e}",
                response_time_ms=elapsed,
                details={"server": server_ip, "container": container_name, "error": str(e)},
            )
    
    # =========================================================================
    # Service-Level Checks
    # =========================================================================
    
    def _detect_service_type(self, service: 'Service') -> str:
        """
        Detect service type for health check routing.
        
        Returns:
            "scheduled" - Has cron schedule
            "worker" - No ports exposed
            "api" - Has ports (HTTP service)
        """
        if service.schedule:
            return "scheduled"
        elif not service.ports:
            return "worker"
        else:
            return "api"
    
    async def check_service(
        self,
        service: 'Service',
        containers: List[Dict[str, Any]],
        check_logs: bool = True,
    ) -> ServiceHealth:
        """
        Check health of a service across all containers.
        
        Automatically detects service type and routes to appropriate check:
        - Scheduled tasks: Validate cron execution
        - Workers (no port): Check running + logs
        - APIs (with port): HTTP/TCP + log errors
        
        Args:
            service: Service definition
            containers: List of container info dicts with 'name', 'server', 'port'
            check_logs: Whether to scan logs for errors (default True)
            
        Returns:
            ServiceHealth
        """
        checks = []
        healthy_count = 0
        degraded_count = 0
        
        service_type = self._detect_service_type(service)
        
        for container in containers:
            container_name = container.get("name")
            server = container.get("server", "localhost")
            port = container.get("port")
            
            # Route to appropriate health check based on service type
            if service_type == "scheduled":
                # Scheduled task: check cron execution
                result = await self.check_scheduled_task(
                    server_ip=server,
                    container_name=container_name,
                    schedule=service.schedule,
                )
            elif service_type == "worker":
                # Worker without port: check running + logs
                result = await self.check_worker_health(
                    server_ip=server,
                    container_name=container_name,
                    check_logs=check_logs,
                )
            elif service.health_check:
                # API with custom health check config
                result = await self._run_service_health_check(
                    service.health_check,
                    server,
                    port,
                    container_name,
                )
                # Add log checking for degraded detection
                if result.is_healthy and check_logs and self.do_token:
                    has_errors, error_lines = await self.check_logs_for_errors(
                        server, container_name, since="5m"
                    )
                    if has_errors:
                        result = HealthCheckResult(
                            status=HealthStatus.DEGRADED,
                            message=f"{result.message} (errors in logs)",
                            response_time_ms=result.response_time_ms,
                            details=result.details,
                            error_lines=error_lines,
                        )
            else:
                # Default API check: unified container health (includes TCP + logs)
                is_remote = self._is_remote(server)
                if is_remote and self.do_token:
                    # Use unified health check (auto-discovers port, checks TCP, analyzes logs)
                    result = await self.check_docker_via_agent(server, container_name, check_logs=check_logs)
                elif not is_remote:
                    # Local check - try via agent on localhost
                    result = await self.check_docker_via_agent("localhost", container_name, check_logs=check_logs) if self.do_token else HealthCheckResult(
                        status=HealthStatus.UNKNOWN,
                        message="No do_token configured for agent health checks",
                        details={"container": container_name, "server": server},
                    )
                else:
                    result = HealthCheckResult(
                        status=HealthStatus.UNKNOWN,
                        message="Remote health check requires do_token for agent authentication",
                        details={"container": container_name, "server": server},
                    )
            
            result.details["container"] = container_name
            result.details["server"] = server
            result.details["service_type"] = service_type
            checks.append(result)
            
            if result.is_healthy:
                healthy_count += 1
            elif result.is_degraded:
                degraded_count += 1
        
        # Determine overall status
        total = len(containers)
        if healthy_count == total:
            status = HealthStatus.HEALTHY
        elif healthy_count + degraded_count == total and healthy_count > 0:
            # Some healthy, some degraded = overall degraded
            status = HealthStatus.DEGRADED
        elif healthy_count == 0 and degraded_count > 0:
            # All degraded = degraded (not unhealthy)
            status = HealthStatus.DEGRADED
        elif healthy_count == 0:
            status = HealthStatus.UNHEALTHY
        else:
            # Mix of healthy/unhealthy = degraded
            status = HealthStatus.DEGRADED
        
        return ServiceHealth(
            service_name=service.name,
            status=status,
            checks=checks,
            containers_healthy=healthy_count,
            containers_total=total,
            last_check=datetime.utcnow(),
        )
    
    async def _run_service_health_check(
        self,
        config: 'ServiceHealthCheck',
        server: str,
        port: Optional[int],
        container_name: str,
    ) -> HealthCheckResult:
        """Run health check based on service config.
        
        For remote servers (not localhost), routes all checks through the unified
        node agent health endpoint which auto-discovers ports and checks connectivity.
        
        For local servers, can perform direct TCP/HTTP checks.
        """
        check_type = config.type
        check_port = config.port or port
        is_remote = self._is_remote(server)
        
        # For remote servers, always use unified agent health check
        # (agent handles TCP/port discovery internally)
        if is_remote and self.do_token:
            return await self.check_docker_via_agent(server, container_name, check_logs=True)
        
        # For local servers, perform direct checks
        if check_type == "http":
            host = server if server != "localhost" else "127.0.0.1"
            url = f"http://{host}:{check_port}{config.path}"
            return await self.check_http(url, timeout=config.timeout)
        
        elif check_type == "tcp":
            host = server if server != "localhost" else "127.0.0.1"
            return await self.check_tcp(host, check_port, timeout=config.timeout)
        
        elif check_type == "exec":
            # Docker exec/inspect health check - via agent
            if self.do_token:
                return await self.check_docker_via_agent(server, container_name)
            else:
                return HealthCheckResult(
                    status=HealthStatus.UNKNOWN,
                    message="Docker exec check requires do_token for agent authentication",
                    details={"container": container_name, "server": server},
                )
        
        else:
            return HealthCheckResult(
                status=HealthStatus.UNKNOWN,
                message=f"Unknown health check type: {check_type}",
            )
    
    # =========================================================================
    # Monitoring Loop
    # =========================================================================
    
    async def monitor(
        self,
        services: Dict[str, 'Service'],
        containers_map: Dict[str, List[Dict[str, Any]]],
        interval: int = 30,
        callback: Optional[Callable[[str, ServiceHealth], None]] = None,
    ):
        """
        Continuous health monitoring loop.
        
        Args:
            services: Dict of service name -> Service
            containers_map: Dict of service name -> container info list
            interval: Check interval in seconds
            callback: Called with (service_name, health) on each check
        """
        self.ctx.log_info(f"Starting health monitor, interval={interval}s")
        
        while True:
            for name, service in services.items():
                containers = containers_map.get(name, [])
                if not containers:
                    continue
                
                try:
                    health = await self.check_service(service, containers)
                    
                    if callback:
                        callback(name, health)
                    
                    # Log status changes
                    if health.status == HealthStatus.UNHEALTHY:
                        self.ctx.log_warning(
                            f"Service {name} unhealthy",
                            healthy=health.containers_healthy,
                            total=health.containers_total,
                        )
                    elif health.status == HealthStatus.DEGRADED:
                        self.ctx.log_warning(
                            f"Service {name} degraded",
                            healthy=health.containers_healthy,
                            total=health.containers_total,
                        )
                        
                except Exception as e:
                    self.ctx.log_error(f"Health check failed for {name}: {e}")
            
            await asyncio.sleep(interval)
    
    # =========================================================================
    # Sync Wrappers (for scripts/CLI)
    # =========================================================================
    
    def check_logs_for_errors_sync(
        self,
        server_ip: str,
        container_name: str,
        since: str = "5m",
        lines: int = 200,
    ) -> Tuple[bool, List[str]]:
        """
        Sync version of check_logs_for_errors.
        
        Returns:
            Tuple of (has_errors, error_lines)
        """
        return asyncio.run(
            self.check_logs_for_errors(server_ip, container_name, since, lines)
        )
    
    def check_scheduled_task_sync(
        self,
        server_ip: str,
        container_name: str,
        schedule: str,
    ) -> HealthCheckResult:
        """
        Sync version of check_scheduled_task.
        
        Args:
            server_ip: Server IP address
            container_name: Container name
            schedule: Cron schedule string (e.g., "0 * * * *")
            
        Returns:
            HealthCheckResult
        """
        return asyncio.run(
            self.check_scheduled_task(server_ip, container_name, schedule)
        )
    
    def check_worker_health_sync(
        self,
        server_ip: str,
        container_name: str,
        check_logs: bool = True,
    ) -> HealthCheckResult:
        """
        Sync version of check_worker_health.
        
        Args:
            server_ip: Server IP address
            container_name: Container name
            check_logs: Whether to check logs for errors
            
        Returns:
            HealthCheckResult
        """
        return asyncio.run(
            self.check_worker_health(server_ip, container_name, check_logs)
        )
    
    def check_service_sync(
        self,
        service: 'Service',
        containers: List[Dict[str, Any]],
        check_logs: bool = True,
    ) -> 'ServiceHealth':
        """
        Sync version of check_service.
        
        Args:
            service: Service configuration
            containers: List of container info dicts with 'server_ip' and 'name'
            check_logs: Whether to check logs for errors
            
        Returns:
            ServiceHealth with aggregated status
        """
        return asyncio.run(
            self.check_service(service, containers, check_logs)
        )


class HealthAggregator:
    """
    Aggregates health across multiple services.
    
    Usage:
        agg = HealthAggregator()
        agg.update("api", api_health)
        agg.update("postgres", pg_health)
        
        overall = agg.get_overall_status()
        report = agg.get_report()
    """
    
    def __init__(self):
        self._services: Dict[str, ServiceHealth] = {}
    
    def update(self, service_name: str, health: ServiceHealth):
        """Update health for a service."""
        self._services[service_name] = health
    
    def get(self, service_name: str) -> Optional[ServiceHealth]:
        """Get health for a service."""
        return self._services.get(service_name)
    
    def get_overall_status(self) -> HealthStatus:
        """Get overall health status across all services."""
        if not self._services:
            return HealthStatus.UNKNOWN
        
        statuses = [h.status for h in self._services.values()]
        
        if all(s == HealthStatus.HEALTHY for s in statuses):
            return HealthStatus.HEALTHY
        elif any(s == HealthStatus.UNHEALTHY for s in statuses):
            return HealthStatus.UNHEALTHY
        elif any(s == HealthStatus.DEGRADED for s in statuses):
            return HealthStatus.DEGRADED
        else:
            return HealthStatus.UNKNOWN
    
    def get_report(self) -> Dict[str, Any]:
        """Get full health report."""
        return {
            "overall_status": self.get_overall_status().value,
            "services": {
                name: health.to_dict()
                for name, health in self._services.items()
            },
            "summary": {
                "total_services": len(self._services),
                "healthy": sum(1 for h in self._services.values() if h.status == HealthStatus.HEALTHY),
                "unhealthy": sum(1 for h in self._services.values() if h.status == HealthStatus.UNHEALTHY),
                "degraded": sum(1 for h in self._services.values() if h.status == HealthStatus.DEGRADED),
            },
            "generated_at": datetime.utcnow().isoformat(),
        }
