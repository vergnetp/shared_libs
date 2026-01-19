"""
Health Monitoring - Container and service health checks.

Supports:
- HTTP health checks
- TCP port checks  
- Docker health status
- Custom exec checks
"""

from __future__ import annotations
import asyncio
import socket
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, Any, List, Optional, Callable
from enum import Enum
from datetime import datetime

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
    from ..docker.client import DockerClient


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
    checked_at: datetime = field(default_factory=datetime.utcnow)
    details: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def is_healthy(self) -> bool:
        return self.status == HealthStatus.HEALTHY
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "message": self.message,
            "response_time_ms": self.response_time_ms,
            "checked_at": self.checked_at.isoformat(),
            "details": self.details,
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
        docker: Optional['DockerClient'] = None,
    ):
        self.ctx = ctx
        self.docker = docker
    
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
    
    async def check_docker_health(
        self,
        container_name: str,
        server: Optional[str] = None,
    ) -> HealthCheckResult:
        """
        Check Docker container health status.
        
        Args:
            container_name: Container name
            server: Server IP (None = local)
            
        Returns:
            HealthCheckResult
        """
        if not self.docker:
            return HealthCheckResult(
                status=HealthStatus.UNKNOWN,
                message="Docker client not available",
            )
        
        start = time.time()
        
        try:
            info = self.docker.inspect(container_name, server)
            elapsed = (time.time() - start) * 1000
            
            if not info:
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message="Container not found",
                    response_time_ms=elapsed,
                    details={"container": container_name},
                )
            
            state = info.get("State", {})
            running = state.get("Running", False)
            health = state.get("Health", {})
            health_status = health.get("Status", "none")
            
            if not running:
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message="Container not running",
                    response_time_ms=elapsed,
                    details={
                        "container": container_name,
                        "state": state.get("Status"),
                        "exit_code": state.get("ExitCode"),
                    },
                )
            
            if health_status == "healthy":
                return HealthCheckResult(
                    status=HealthStatus.HEALTHY,
                    message="Container healthy",
                    response_time_ms=elapsed,
                    details={"container": container_name, "health_status": health_status},
                )
            elif health_status == "unhealthy":
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message="Container unhealthy",
                    response_time_ms=elapsed,
                    details={
                        "container": container_name,
                        "health_status": health_status,
                        "failing_streak": health.get("FailingStreak"),
                    },
                )
            elif health_status == "starting":
                return HealthCheckResult(
                    status=HealthStatus.DEGRADED,
                    message="Container starting",
                    response_time_ms=elapsed,
                    details={"container": container_name, "health_status": health_status},
                )
            else:
                # No health check configured, but container is running
                return HealthCheckResult(
                    status=HealthStatus.HEALTHY,
                    message="Container running (no health check)",
                    response_time_ms=elapsed,
                    details={"container": container_name, "running": True},
                )
                
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return HealthCheckResult(
                status=HealthStatus.UNKNOWN,
                message=f"Check failed: {e}",
                response_time_ms=elapsed,
                details={"container": container_name, "error": str(e)},
            )
    
    # =========================================================================
    # Service-Level Checks
    # =========================================================================
    
    async def check_service(
        self,
        service: 'Service',
        containers: List[Dict[str, Any]],
    ) -> ServiceHealth:
        """
        Check health of a service across all containers.
        
        Args:
            service: Service definition
            containers: List of container info dicts with 'name', 'server', 'port'
            
        Returns:
            ServiceHealth
        """
        checks = []
        healthy_count = 0
        
        for container in containers:
            container_name = container.get("name")
            server = container.get("server", "localhost")
            port = container.get("port")
            
            # Determine check type from service config
            if service.health_check:
                result = await self._run_service_health_check(
                    service.health_check,
                    server,
                    port,
                    container_name,
                )
            else:
                # Default: check docker health
                result = await self.check_docker_health(container_name, server)
            
            result.details["container"] = container_name
            result.details["server"] = server
            checks.append(result)
            
            if result.is_healthy:
                healthy_count += 1
        
        # Determine overall status
        total = len(containers)
        if healthy_count == total:
            status = HealthStatus.HEALTHY
        elif healthy_count == 0:
            status = HealthStatus.UNHEALTHY
        else:
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
        """Run health check based on service config."""
        check_type = config.type
        check_port = config.port or port
        
        if check_type == "http":
            host = server if server != "localhost" else "127.0.0.1"
            url = f"http://{host}:{check_port}{config.path}"
            return await self.check_http(url, timeout=config.timeout)
        
        elif check_type == "tcp":
            host = server if server != "localhost" else "127.0.0.1"
            return await self.check_tcp(host, check_port, timeout=config.timeout)
        
        elif check_type == "exec":
            # Docker exec health check
            return await self.check_docker_health(container_name, server)
        
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
