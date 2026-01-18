"""
Task Handlers - Implementations for scheduled tasks.

Each handler receives a ScheduledTask and returns a result.
"""

import logging
from typing import Any, Dict, List
from .task_scheduler import ScheduledTask, TaskType


logger = logging.getLogger(__name__)


async def health_check_handler(task: ScheduledTask) -> Dict[str, Any]:
    """
    Health check handler - checks all managed servers and containers.
    
    Config options:
        - do_token: DigitalOcean token (required) - used to discover managed droplets
        - user_id: User ID for API key derivation (required)
        - server_ips: Optional list of specific IPs to check (overrides auto-discovery)
        - auto_restart: Whether to restart unhealthy containers (default: False)
        - auto_replace: Whether to replace unreachable servers (default: False) - NOT IMPLEMENTED
    
    The handler will:
    1. Query all managed droplets from DigitalOcean (or use provided server_ips)
    2. Check health of each server via node_agent
    3. Optionally restart unhealthy containers
    """
    from ..node_agent import NodeAgentClient
    from ..cloud import DOClient, generate_node_agent_key
    
    config = task.config
    do_token = config.get("do_token")
    user_id = config.get("user_id", "")
    auto_restart = config.get("auto_restart", False)
    
    if not do_token:
        return {"error": "No DO token configured - cannot discover servers"}
    
    # Generate API key from token
    api_key = generate_node_agent_key(do_token)
    
    # Discover managed servers (or use provided list)
    server_ips = config.get("server_ips", [])
    if not server_ips:
        try:
            client = DOClient(do_token)
            droplets = client.list_droplets()  # Only returns managed droplets
            server_ips = [d.ip for d in droplets if d.ip and d.is_active]
            logger.info(f"Discovered {len(server_ips)} managed servers for health check")
        except Exception as e:
            return {"error": f"Failed to discover servers: {e}"}
    
    if not server_ips:
        return {"status": "ok", "message": "No servers to check", "servers": []}
    
    results = {
        "checked": 0,
        "healthy": 0,
        "unhealthy": 0,
        "unreachable": 0,
        "restarted": [],
        "servers": [],
    }
    
    for ip in server_ips:
        try:
            async with NodeAgentClient(ip, api_key, timeout=15) as client:
                health = await client.check_containers_health()
                
                if not health.success:
                    results["unreachable"] += 1
                    results["servers"].append({"ip": ip, "status": "unreachable", "error": health.error})
                    continue
                
                summary = health.data.get("summary", {})
                containers = health.data.get("containers", [])
                
                results["checked"] += 1
                results["healthy"] += summary.get("healthy", 0)
                results["unhealthy"] += summary.get("unhealthy", 0)
                
                server_result = {
                    "ip": ip,
                    "status": "online",
                    "containers": summary.get("total", 0),
                    "healthy": summary.get("healthy", 0),
                    "unhealthy": summary.get("unhealthy", 0),
                }
                
                # Auto-restart unhealthy containers
                if auto_restart:
                    unhealthy_containers = [c for c in containers if c.get("health") == "unhealthy"]
                    for container in unhealthy_containers:
                        name = container.get("name")
                        try:
                            restart_result = await client.restart_container(name)
                            if restart_result.success:
                                results["restarted"].append({"ip": ip, "container": name})
                                logger.info(f"Auto-restarted {name} on {ip}")
                        except Exception as e:
                            logger.error(f"Failed to restart {name} on {ip}: {e}")
                
                results["servers"].append(server_result)
                
        except Exception as e:
            results["unreachable"] += 1
            results["servers"].append({"ip": ip, "status": "error", "error": str(e)})
    
    # Summary status
    if results["unreachable"] > 0:
        results["status"] = "degraded"
    elif results["unhealthy"] > 0:
        results["status"] = "unhealthy"
    else:
        results["status"] = "healthy"
    
    return results


async def auto_restart_handler(task: ScheduledTask) -> Dict[str, Any]:
    """
    Auto-restart handler - restarts unhealthy containers.
    
    This is a simplified version that only restarts, doesn't check health first.
    Use health_check_handler with auto_restart=True for combined behavior.
    
    Config options:
        - server_ips: List of IPs to check
        - containers: Specific containers to restart (optional)
    """
    from ..node_agent import NodeAgentClient
    
    config = task.config
    server_ips = config.get("server_ips", [])
    target_containers = config.get("containers", [])  # Empty = check all
    api_key = config.get("api_key", "")
    
    if not api_key:
        return {"error": "No API key configured"}
    
    results = {
        "checked": 0,
        "restarted": [],
        "failed": [],
    }
    
    for ip in server_ips:
        try:
            async with NodeAgentClient(ip, api_key, timeout=15) as client:
                # Get health status
                health = await client.check_containers_health()
                if not health.success:
                    continue
                
                containers = health.data.get("containers", [])
                
                for container in containers:
                    name = container.get("name", "")
                    health_status = container.get("health", "")
                    
                    # Skip if not targeting this container
                    if target_containers and name not in target_containers:
                        continue
                    
                    results["checked"] += 1
                    
                    # Restart if unhealthy
                    if health_status == "unhealthy":
                        try:
                            restart_result = await client.restart_container(name)
                            if restart_result.success:
                                results["restarted"].append({"ip": ip, "container": name})
                            else:
                                results["failed"].append({"ip": ip, "container": name, "error": restart_result.error})
                        except Exception as e:
                            results["failed"].append({"ip": ip, "container": name, "error": str(e)})
                            
        except Exception as e:
            logger.error(f"Auto-restart failed for {ip}: {e}")
    
    return results


async def backup_handler(task: ScheduledTask) -> Dict[str, Any]:
    """
    Backup handler - triggers backup for stateful services.
    
    Config options:
        - server_ip: Server running the container
        - container_name: Container to backup
        - backup_type: 'postgres', 'redis', 'files'
        - backup_command: Custom backup command (optional)
    """
    from ..node_agent import NodeAgentClient
    
    config = task.config
    server_ip = config.get("server_ip")
    container_name = config.get("container_name")
    backup_type = config.get("backup_type", "postgres")
    api_key = config.get("api_key", "")
    
    if not api_key or not server_ip or not container_name:
        return {"error": "Missing required config: api_key, server_ip, container_name"}
    
    # Default backup commands by type
    backup_commands = {
        "postgres": ["pg_dump", "-U", "postgres", "-F", "c", "-f", "/tmp/backup.dump", "postgres"],
        "redis": ["redis-cli", "BGSAVE"],
        "files": ["tar", "-czf", "/tmp/backup.tar.gz", "/data"],
    }
    
    command = config.get("backup_command") or backup_commands.get(backup_type, [])
    
    if not command:
        return {"error": f"Unknown backup type: {backup_type}"}
    
    try:
        async with NodeAgentClient(server_ip, api_key, timeout=300) as client:
            result = await client.exec_in_container(container_name, command, timeout=300)
            
            if result.success:
                return {
                    "status": "success",
                    "server": server_ip,
                    "container": container_name,
                    "output": result.data.get("stdout", "")[:500],  # Truncate
                }
            else:
                return {
                    "status": "failed",
                    "error": result.error,
                }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# Handler registry
TASK_HANDLERS = {
    TaskType.HEALTH_CHECK: health_check_handler,
    TaskType.AUTO_RESTART: auto_restart_handler,
    TaskType.BACKUP: backup_handler,
}


def register_all_handlers(scheduler):
    """Register all built-in handlers with the scheduler."""
    for task_type, handler in TASK_HANDLERS.items():
        scheduler.register_handler(task_type, handler)
