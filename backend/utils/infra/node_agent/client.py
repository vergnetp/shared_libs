"""
Node Agent Client - HTTP client for SSH-free deployments

Use this client to interact with node agents running on droplets.
"""

import httpx
from typing import Dict, List, Optional, Any
from dataclasses import dataclass


@dataclass
class AgentResponse:
    """Response from node agent."""
    success: bool
    data: Dict[str, Any]
    error: Optional[str] = None


class NodeAgentClient:
    """
    Client for interacting with node agents on droplets.
    
    Example:
        client = NodeAgentClient("206.189.122.244", "your-api-key")
        
        # Check health
        health = await client.ping()
        
        # Deploy container
        result = await client.run_container(
            name="myapp-prod",
            image="myregistry/myapp:v1",
            port=8000,
            env_vars={"DATABASE_URL": "..."}
        )
    """
    
    AGENT_PORT = 9999
    
    def __init__(self, server_ip: str, api_key: str, timeout: int = 30):
        self.server_ip = server_ip
        self.api_key = api_key
        self.timeout = timeout
        self.base_url = f"http://{server_ip}:{self.AGENT_PORT}"
    
    @property
    def headers(self) -> Dict[str, str]:
        return {"X-API-Key": self.api_key}
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        json_data: Dict = None,
        timeout: int = None,
    ) -> AgentResponse:
        """Make authenticated request to node agent."""
        timeout = timeout or self.timeout
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{endpoint}",
                    headers=self.headers,
                    json=json_data,
                    timeout=timeout,
                )
                response.raise_for_status()
                return AgentResponse(success=True, data=response.json())
                
        except httpx.ConnectError:
            return AgentResponse(
                success=False,
                data={},
                error=f"Cannot connect to agent at {self.server_ip}:{self.AGENT_PORT}"
            )
        except httpx.HTTPStatusError as e:
            return AgentResponse(
                success=False,
                data={},
                error=f"HTTP {e.response.status_code}: {e.response.text}"
            )
        except httpx.TimeoutException:
            return AgentResponse(
                success=False,
                data={},
                error=f"Request timed out after {timeout}s"
            )
        except Exception as e:
            return AgentResponse(success=False, data={}, error=str(e))
    
    # =========================================================================
    # Health
    # =========================================================================
    
    async def ping(self) -> AgentResponse:
        """Check if agent is alive."""
        return await self._request("GET", "/ping")
    
    async def health(self) -> AgentResponse:
        """Get comprehensive health status."""
        return await self._request("GET", "/health")
    
    # =========================================================================
    # Containers
    # =========================================================================
    
    async def list_containers(self) -> AgentResponse:
        """List all containers on the server."""
        return await self._request("GET", "/containers")
    
    async def run_container(
        self,
        name: str,
        image: str,
        ports: Dict[str, str] = None,
        env_vars: Dict[str, str] = None,
        volumes: List[str] = None,
        network: str = None,
        restart_policy: str = "unless-stopped",
        command: List[str] = None,
    ) -> AgentResponse:
        """
        Start a container.
        
        Args:
            name: Container name
            image: Docker image (e.g., "myapp:latest")
            ports: Port mappings {"8080": "80"}
            env_vars: Environment variables
            volumes: Volume mounts ["/host/path:/container/path"]
            network: Docker network name
            restart_policy: Restart policy
            command: Command to run
        """
        payload = {
            "name": name,
            "image": image,
            "ports": ports or {},
            "env_vars": env_vars or {},
            "volumes": volumes or [],
            "restart_policy": restart_policy,
        }
        
        if network:
            payload["network"] = network
        if command:
            payload["command"] = command
        
        return await self._request("POST", "/containers/run", payload, timeout=120)
    
    async def stop_container(self, name: str) -> AgentResponse:
        """Stop a container."""
        return await self._request("POST", f"/containers/{name}/stop", timeout=30)
    
    async def remove_container(self, name: str) -> AgentResponse:
        """Remove a container."""
        return await self._request("POST", f"/containers/{name}/remove")
    
    async def container_status(self, name: str) -> AgentResponse:
        """Get container status."""
        return await self._request("GET", f"/containers/{name}/status")
    
    async def container_logs(self, name: str, lines: int = 100) -> AgentResponse:
        """Get container logs."""
        return await self._request("GET", f"/containers/{name}/logs?lines={lines}")
    
    # =========================================================================
    # Images
    # =========================================================================
    
    async def pull_image(self, image: str) -> AgentResponse:
        """Pull a Docker image."""
        return await self._request(
            "POST", "/images/pull",
            {"image": image},
            timeout=600  # 10 min for large images
        )
    
    async def build_image(
        self,
        context_path: str = "/app",
        image_tag: str = "app:latest",
        dockerfile: str = None,
    ) -> AgentResponse:
        """Build Docker image from uploaded code."""
        return await self._request(
            "POST", "/docker/build",
            {
                "context_path": context_path,
                "image_tag": image_tag,
                "dockerfile": dockerfile,
            },
            timeout=600  # 10 min for builds
        )
    
    async def upload_tar(
        self,
        tar_data: bytes,
        extract_path: str = "/app",
    ) -> AgentResponse:
        """Upload and extract tar.gz archive."""
        import base64
        return await self._request(
            "POST", "/upload/tar",
            {
                "data": base64.b64encode(tar_data).decode(),
                "extract_path": extract_path,
            },
            timeout=120
        )
    
    async def git_clone(
        self,
        repo_url: str,
        branch: str = "main",
        target_path: str = "/app",
        access_token: str = None,
    ) -> AgentResponse:
        """Clone a git repository."""
        return await self._request(
            "POST", "/git/clone",
            {
                "repo_url": repo_url,
                "branch": branch,
                "target_path": target_path,
                "access_token": access_token,
            },
            timeout=300  # 5 min for large repos
        )
    
    # =========================================================================
    # Files
    # =========================================================================
    
    async def write_file(
        self,
        path: str,
        content: str,
        permissions: str = "644"
    ) -> AgentResponse:
        """Write a file to the server."""
        return await self._request("POST", "/files/write", {
            "path": path,
            "content": content,
            "permissions": permissions,
        })
    
    # =========================================================================
    # Services
    # =========================================================================
    
    async def restart_service(self, name: str) -> AgentResponse:
        """Restart a system service (nginx, docker, node-agent)."""
        return await self._request("POST", f"/services/{name}/restart")
    
    async def reload_nginx(self) -> AgentResponse:
        """Reload nginx configuration."""
        return await self._request("POST", "/nginx/reload")
    
    # =========================================================================
    # Firewall
    # =========================================================================
    
    async def firewall_status(self) -> AgentResponse:
        """Get UFW firewall status."""
        return await self._request("GET", "/firewall/status")
    
    async def firewall_allow(
        self,
        port: int,
        protocol: str = "tcp",
        source: str = None
    ) -> AgentResponse:
        """Add firewall allow rule."""
        return await self._request("POST", "/firewall/allow", {
            "port": port,
            "protocol": protocol,
            "source": source,
        })
    
    # =========================================================================
    # High-level operations
    # =========================================================================
    
    async def deploy_container(
        self,
        name: str,
        image: str,
        port: int,
        env_vars: Dict[str, str] = None,
        pull_image: bool = True,
        remove_existing: bool = True,
    ) -> Dict[str, Any]:
        """
        High-level deploy operation.
        
        1. Stop & remove existing container (if remove_existing)
        2. Pull image (if pull_image)
        3. Start container
        4. Verify running
        
        Returns dict with success, logs, and container info.
        """
        logs = []
        
        def log(msg: str):
            logs.append(msg)
        
        # Health check
        log(f"Connecting to {self.server_ip}...")
        health = await self.ping()
        if not health.success:
            return {"success": False, "error": health.error, "logs": logs}
        log(f"✅ Agent alive (version: {health.data.get('version', '?')})")
        
        # Remove existing
        if remove_existing:
            log(f"Stopping existing container: {name}...")
            await self.stop_container(name)
            await self.remove_container(name)
            log("  Done")
        
        # Pull image
        if pull_image:
            log(f"Pulling image: {image}...")
            result = await self.pull_image(image)
            if result.success:
                log("✅ Image pulled")
            else:
                log(f"⚠️ Pull failed (may exist): {result.error}")
        
        # Start container
        log(f"Starting container: {name}...")
        result = await self.run_container(
            name=name,
            image=image,
            ports={str(port): str(port)},
            env_vars=env_vars or {},
        )
        
        if not result.success:
            return {"success": False, "error": result.error, "logs": logs}
        
        container_id = result.data.get("container_id", "unknown")
        log(f"✅ Container started: {container_id[:12]}")
        
        # Verify
        import asyncio
        await asyncio.sleep(2)
        
        status = await self.container_status(name)
        if status.success and status.data.get("running"):
            log(f"✅ Container running")
            return {
                "success": True,
                "container_id": container_id,
                "url": f"http://{self.server_ip}:{port}",
                "logs": logs,
            }
        else:
            # Get logs for debugging
            container_logs = await self.container_logs(name, lines=50)
            if container_logs.success:
                log(f"Container logs: {container_logs.data.get('logs', '')[:500]}")
            
            return {
                "success": False,
                "error": "Container not running",
                "logs": logs,
            }
