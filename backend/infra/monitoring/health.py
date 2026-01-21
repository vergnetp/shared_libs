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

# Import NodeAgentClient for remote health checks (avoids direct TCP/SSH)
from ..node_agent.client import NodeAgentClient


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
    
    async def check_tcp_via_agent(
        self,
        server_ip: str,
        port: int,
        timeout: int = 5,
    ) -> HealthCheckResult:
        """
        TCP health check via node agent API.
        
        Routes the check through the node agent running on the target server,
        avoiding the need for direct TCP connections to internal ports.
        
        Args:
            server_ip: Server IP address
            port: Port to check (on localhost from agent's perspective)
            timeout: Timeout in seconds
            
        Returns:
            HealthCheckResult
        """
        client = self._get_agent_client(server_ip)
        if not client:
            return HealthCheckResult(
                status=HealthStatus.UNKNOWN,
                message="No DO token configured for remote health checks",
                details={"server": server_ip, "port": port},
            )
        
        start = time.time()
        try:
            result = await client.health_tcp(port=port, timeout=timeout)
            elapsed = (time.time() - start) * 1000
            
            if result.success and result.data.get("status") == "healthy":
                return HealthCheckResult(
                    status=HealthStatus.HEALTHY,
                    message=f"Port {port} is open",
                    response_time_ms=result.data.get("response_time_ms", elapsed),
                    details={"server": server_ip, "port": port, "via": "node_agent"},
                )
            else:
                error = result.data.get("error", result.error or "Unknown error")
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message=f"Port {port} check failed: {error}",
                    response_time_ms=elapsed,
                    details={"server": server_ip, "port": port, "via": "node_agent", "error": error},
                )
                
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"Agent health check failed: {e}",
                response_time_ms=elapsed,
                details={"server": server_ip, "port": port, "error": str(e)},
            )
    
    async def check_http_via_agent(
        self,
        server_ip: str,
        port: int,
        path: str = "/",
        timeout: int = 10,
        method: str = "GET",
    ) -> HealthCheckResult:
        """
        HTTP health check via node agent API.
        
        Routes the check through the node agent, which makes the HTTP request
        to localhost. This avoids direct connections to internal ports.
        
        Args:
            server_ip: Server IP address
            port: Port to check
            path: HTTP path (default "/")
            timeout: Timeout in seconds
            method: HTTP method (default "GET")
            
        Returns:
            HealthCheckResult
        """
        client = self._get_agent_client(server_ip)
        if not client:
            return HealthCheckResult(
                status=HealthStatus.UNKNOWN,
                message="No DO token configured for remote health checks",
                details={"server": server_ip, "port": port, "path": path},
            )
        
        start = time.time()
        try:
            result = await client.health_http(
                port=port,
                path=path,
                timeout=timeout,
                method=method,
            )
            elapsed = (time.time() - start) * 1000
            
            if result.success and result.data.get("status") == "healthy":
                return HealthCheckResult(
                    status=HealthStatus.HEALTHY,
                    message=f"HTTP {method} {path} returned {result.data.get('status_code', 200)}",
                    response_time_ms=result.data.get("response_time_ms", elapsed),
                    details={
                        "server": server_ip,
                        "port": port,
                        "path": path,
                        "status_code": result.data.get("status_code"),
                        "via": "node_agent",
                    },
                )
            else:
                error = result.data.get("error", result.error or "Unknown error")
                status_code = result.data.get("status_code")
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message=f"HTTP check failed: {error}" + (f" (status {status_code})" if status_code else ""),
                    response_time_ms=elapsed,
                    details={
                        "server": server_ip,
                        "port": port,
                        "path": path,
                        "status_code": status_code,
                        "via": "node_agent",
                        "error": error,
                    },
                )
                
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"Agent health check failed: {e}",
                response_time_ms=elapsed,
                details={"server": server_ip, "port": port, "path": path, "error": str(e)},
            )
    
    async def check_docker_via_agent(
        self,
        server_ip: str,
        container_name: str,
    ) -> HealthCheckResult:
        """
        Docker container health check via node agent API.
        
        Uses the node agent's inspect endpoint to check container status,
        avoiding direct Docker API or SSH connections.
        
        Args:
            server_ip: Server IP address
            container_name: Container name
            
        Returns:
            HealthCheckResult
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
            result = await client.inspect(container_name)
            elapsed = (time.time() - start) * 1000
            
            if not result.success:
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message=f"Container not found or error: {result.error}",
                    response_time_ms=elapsed,
                    details={"server": server_ip, "container": container_name, "via": "node_agent"},
                )
            
            info = result.data
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
                        "server": server_ip,
                        "container": container_name,
                        "state": state.get("Status"),
                        "exit_code": state.get("ExitCode"),
                        "via": "node_agent",
                    },
                )
            
            if health_status == "healthy":
                return HealthCheckResult(
                    status=HealthStatus.HEALTHY,
                    message="Container healthy",
                    response_time_ms=elapsed,
                    details={
                        "server": server_ip,
                        "container": container_name,
                        "health_status": health_status,
                        "via": "node_agent",
                    },
                )
            elif health_status == "unhealthy":
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message="Container unhealthy",
                    response_time_ms=elapsed,
                    details={
                        "server": server_ip,
                        "container": container_name,
                        "health_status": health_status,
                        "failing_streak": health.get("FailingStreak"),
                        "via": "node_agent",
                    },
                )
            elif health_status == "starting":
                return HealthCheckResult(
                    status=HealthStatus.DEGRADED,
                    message="Container starting",
                    response_time_ms=elapsed,
                    details={
                        "server": server_ip,
                        "container": container_name,
                        "health_status": health_status,
                        "via": "node_agent",
                    },
                )
            else:
                # No health check configured, but running
                return HealthCheckResult(
                    status=HealthStatus.HEALTHY,
                    message="Container running (no health check)",
                    response_time_ms=elapsed,
                    details={
                        "server": server_ip,
                        "container": container_name,
                        "running": True,
                        "via": "node_agent",
                    },
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
                # Default: check docker health via agent
                is_remote = self._is_remote(server)
                if is_remote and self.do_token:
                    result = await self.check_docker_via_agent(server, container_name)
                elif not is_remote:
                    # Local check - try via agent on localhost
                    result = await self.check_docker_via_agent("localhost", container_name) if self.do_token else HealthCheckResult(
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
        """Run health check based on service config.
        
        For remote servers (not localhost), routes checks through node agent API
        to avoid direct TCP connections to internal ports.
        """
        check_type = config.type
        check_port = config.port or port
        is_remote = self._is_remote(server)
        
        if check_type == "http":
            if is_remote and self.do_token:
                # Route through node agent for remote servers
                return await self.check_http_via_agent(
                    server_ip=server,
                    port=check_port,
                    path=config.path,
                    timeout=config.timeout,
                )
            else:
                # Local check or no agent configured
                host = server if server != "localhost" else "127.0.0.1"
                url = f"http://{host}:{check_port}{config.path}"
                return await self.check_http(url, timeout=config.timeout)
        
        elif check_type == "tcp":
            if is_remote and self.do_token:
                # Route through node agent for remote servers
                return await self.check_tcp_via_agent(
                    server_ip=server,
                    port=check_port,
                    timeout=config.timeout,
                )
            else:
                # Local check or no agent configured
                host = server if server != "localhost" else "127.0.0.1"
                return await self.check_tcp(host, check_port, timeout=config.timeout)
        
        elif check_type == "exec":
            # Docker exec/inspect health check - always via agent
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
