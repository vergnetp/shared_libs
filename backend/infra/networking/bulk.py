"""
Bulk Nginx Operations - Multi-server nginx management.

Routes should be thin wrappers around this service:
    
    # In route (5 lines)
    service = BulkNginxService(do_token, user_id)
    result = await service.ensure_on_servers(server_ips)
    return result
    
    # In CLI (same code)
    service = BulkNginxService(do_token, user_id)
    result = await service.ensure_on_servers(server_ips)
    print(result)
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable

from .service import NginxService, NginxResult
from ..node_agent import NodeAgentClient
from ..cloud import generate_node_agent_key


@dataclass
class BulkNginxResult:
    """Result from bulk nginx operations."""
    success: bool
    results: List[Dict[str, Any]] = field(default_factory=list)
    fixed: int = 0
    already_ok: int = 0
    failed: int = 0
    message: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "results": self.results,
            "fixed": self.fixed,
            "already_ok": self.already_ok,
            "failed": self.failed,
            "message": self.message,
        }


class BulkNginxService:
    """
    Bulk nginx operations across multiple servers.
    
    Usage:
        service = BulkNginxService(do_token, user_id)
        
        # Ensure nginx running on all servers
        result = await service.ensure_on_servers(["1.2.3.4", "5.6.7.8"])
        
        # Setup sidecar on all servers
        result = await service.setup_sidecar_on_servers(
            server_ips=["1.2.3.4"],
            project="myapp",
            environment="prod",
            service="api",
            container_port=8000,
        )
    """
    
    def __init__(
        self,
        do_token: str,
        user_id: str,
        log: Callable[[str], None] = None,
    ):
        self.do_token = do_token
        self.user_id = user_id
        self.api_key = generate_node_agent_key(do_token)
        self.log = log or (lambda msg: None)
    
    def _get_nginx_service(self, ip: str) -> NginxService:
        """Create NginxService for a server."""
        client = NodeAgentClient(ip, self.do_token)
        return NginxService(client, log=self.log)
    
    # =========================================================================
    # Ensure Nginx Running
    # =========================================================================
    
    async def ensure_on_servers(self, server_ips: List[str]) -> BulkNginxResult:
        """
        Ensure nginx sidecar is running on specified servers.
        
        Creates nginx container if not exists, starts if stopped.
        Also fixes common nginx config issues like server_names_hash_bucket_size.
        
        Args:
            server_ips: List of server IPs
            
        Returns:
            BulkNginxResult with per-server status
        """
        results = []
        fixed = 0
        already_ok = 0
        failed = 0
        
        for ip in server_ips:
            try:
                nginx = self._get_nginx_service(ip)
                result = await nginx.ensure_running()
                
                if result.success:
                    status = result.data.get("status", "started") if result.data else "started"
                    if status == "already_running":
                        already_ok += 1
                    else:
                        fixed += 1
                    
                    # Fix hash bucket size
                    try:
                        await self._fix_hash_bucket_size(ip)
                    except Exception:
                        pass  # Best effort
                    
                    results.append({
                        "ip": ip,
                        "status": status,
                        "success": True,
                    })
                else:
                    failed += 1
                    results.append({
                        "ip": ip,
                        "status": "failed",
                        "success": False,
                        "error": result.error,
                    })
                    
            except Exception as e:
                failed += 1
                results.append({
                    "ip": ip,
                    "status": "error",
                    "success": False,
                    "error": str(e),
                })
        
        return BulkNginxResult(
            success=failed == 0,
            results=results,
            fixed=fixed,
            already_ok=already_ok,
            failed=failed,
            message=f"Fixed {fixed}, already OK {already_ok}, failed {failed}",
        )
    
    async def _fix_hash_bucket_size(self, ip: str) -> None:
        """Fix nginx server_names_hash_bucket_size if needed."""
        client = NodeAgentClient(ip, self.do_token)
        
        fix_script = '''
if ! grep -q "server_names_hash_bucket_size" /etc/nginx/nginx.conf; then
    sed -i '/^http {/a\\    server_names_hash_bucket_size 128;' /etc/nginx/nginx.conf
    nginx -t && nginx -s reload
fi
'''
        if hasattr(client, 'exec_command'):
            await client.exec_command(fix_script)
    
    # =========================================================================
    # Setup Sidecar
    # =========================================================================
    
    async def setup_sidecar_on_servers(
        self,
        server_ips: List[str],
        project: str,
        environment: str,
        service: str,
        container_name: str,
        container_port: int,
        is_stateful: bool = False,
    ) -> BulkNginxResult:
        """
        Setup nginx sidecar config on multiple servers.
        
        Args:
            server_ips: List of server IPs
            project: Project name
            environment: Environment (prod/staging/dev)
            service: Service name
            container_name: Container name
            container_port: Container's internal port
            is_stateful: Is stateful service (postgres, redis)
            
        Returns:
            BulkNginxResult with per-server status and internal_port
        """
        results = []
        success_count = 0
        failed = 0
        internal_port = None
        
        for ip in server_ips:
            try:
                nginx = self._get_nginx_service(ip)
                result = await nginx.setup_service_sidecar(
                    user_id=self.user_id,
                    project=project,
                    environment=environment,
                    service=service,
                    container_name=container_name,
                    container_port=container_port,
                    is_stateful=is_stateful,
                )
                
                if result.success:
                    success_count += 1
                    internal_port = result.data.get("internal_port") if result.data else None
                    results.append({
                        "ip": ip,
                        "success": True,
                        "internal_port": internal_port,
                    })
                else:
                    failed += 1
                    results.append({
                        "ip": ip,
                        "success": False,
                        "error": result.error,
                    })
                    
            except Exception as e:
                failed += 1
                results.append({
                    "ip": ip,
                    "success": False,
                    "error": str(e),
                })
        
        return BulkNginxResult(
            success=failed == 0,
            results=results,
            fixed=success_count,
            failed=failed,
            message=f"Configured {success_count} servers, failed {failed}",
        )
    
    # =========================================================================
    # Update Sidecar
    # =========================================================================
    
    async def update_sidecar_on_servers(
        self,
        server_ips: List[str],
        project: str,
        environment: str,
        service: str,
        backends: List[Dict[str, Any]],
    ) -> BulkNginxResult:
        """
        Update nginx sidecar backends on multiple servers.
        
        Args:
            server_ips: List of server IPs
            project: Project name
            environment: Environment
            service: Service name
            backends: List of backends [{"ip": "...", "port": ...}]
        """
        results = []
        success_count = 0
        failed = 0
        
        for ip in server_ips:
            try:
                nginx = self._get_nginx_service(ip)
                result = await nginx.update_sidecar_backends(
                    user_id=self.user_id,
                    project=project,
                    environment=environment,
                    service=service,
                    backends=backends,
                )
                
                if result.success:
                    success_count += 1
                    results.append({"ip": ip, "success": True})
                else:
                    failed += 1
                    results.append({"ip": ip, "success": False, "error": result.error})
                    
            except Exception as e:
                failed += 1
                results.append({"ip": ip, "success": False, "error": str(e)})
        
        return BulkNginxResult(
            success=failed == 0,
            results=results,
            fixed=success_count,
            failed=failed,
            message=f"Updated {success_count} servers, failed {failed}",
        )
    
    # =========================================================================
    # HTTP Load Balancer
    # =========================================================================
    
    async def setup_lb_on_servers(
        self,
        server_ips: List[str],
        name: str,
        backends: List[Dict[str, Any]],
        listen_port: int = 80,
        domain: str = None,
        lb_method: str = "least_conn",
        health_check: bool = True,
    ) -> BulkNginxResult:
        """
        Setup HTTP load balancer on multiple servers.
        
        Args:
            server_ips: List of server IPs to configure
            name: LB name (used for upstream and config file)
            backends: List of backends [{"ip": "...", "port": ...}]
            listen_port: Port to listen on (default 80)
            domain: Optional domain name
            lb_method: Load balancing method (least_conn, ip_hash, round_robin)
            health_check: Enable health checks
        """
        results = []
        success_count = 0
        failed = 0
        
        for ip in server_ips:
            try:
                nginx = self._get_nginx_service(ip)
                result = await nginx.setup_http_lb(
                    name=name,
                    backends=backends,
                    listen_port=listen_port,
                    domain=domain,
                    lb_method=lb_method,
                    health_check=health_check,
                )
                
                if result.success:
                    success_count += 1
                    results.append({"ip": ip, "success": True, "data": result.data})
                else:
                    failed += 1
                    results.append({"ip": ip, "success": False, "error": result.error})
                    
            except Exception as e:
                failed += 1
                results.append({"ip": ip, "success": False, "error": str(e)})
        
        return BulkNginxResult(
            success=failed == 0,
            results=results,
            fixed=success_count,
            failed=failed,
            message=f"Configured LB on {success_count} servers, failed {failed}",
        )
    
    async def remove_lb_on_servers(
        self,
        server_ips: List[str],
        name: str,
    ) -> BulkNginxResult:
        """Remove HTTP load balancer from multiple servers."""
        results = []
        success_count = 0
        failed = 0
        
        for ip in server_ips:
            try:
                nginx = self._get_nginx_service(ip)
                result = await nginx.remove_http_lb(name)
                
                if result.success:
                    success_count += 1
                    results.append({"ip": ip, "success": True})
                else:
                    failed += 1
                    results.append({"ip": ip, "success": False, "error": result.error})
                    
            except Exception as e:
                failed += 1
                results.append({"ip": ip, "success": False, "error": str(e)})
        
        return BulkNginxResult(
            success=failed == 0,
            results=results,
            fixed=success_count,
            failed=failed,
            message=f"Removed LB from {success_count} servers, failed {failed}",
        )


# Sync wrapper for CLI usage
class SyncBulkNginxService:
    """Synchronous wrapper for CLI/scripts."""
    
    def __init__(self, do_token: str, user_id: str, log: Callable[[str], None] = None):
        self._async = BulkNginxService(do_token, user_id, log)
    
    def _run(self, coro):
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    
    def ensure_on_servers(self, server_ips: List[str]) -> BulkNginxResult:
        return self._run(self._async.ensure_on_servers(server_ips))
    
    def setup_sidecar_on_servers(self, **kwargs) -> BulkNginxResult:
        return self._run(self._async.setup_sidecar_on_servers(**kwargs))
    
    def setup_lb_on_servers(self, **kwargs) -> BulkNginxResult:
        return self._run(self._async.setup_lb_on_servers(**kwargs))
    
    def remove_lb_on_servers(self, server_ips: List[str], name: str) -> BulkNginxResult:
        return self._run(self._async.remove_lb_on_servers(server_ips, name))
