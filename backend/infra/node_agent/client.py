"""
Node Agent Client - HTTP client for SSH-free deployments

Uses http_client for automatic retries.

Use this client to interact with node agents running on droplets.
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass

# httpx is used directly for multipart/streaming uploads (load_image, load_image_stream)
# These features aren't supported by http_client module, and for these operations:
# - No retry: retrying a 500MB upload is wasteful
# - No circuit breaker: one failure doesn't mean we should stop
# - Long timeouts: 600s for large Docker images
import httpx


@dataclass
class AgentResponse:
    """Response from node agent."""
    success: bool
    data: Dict[str, Any] = None
    error: Optional[str] = None


class NodeAgentClient:
    """
    Client for interacting with node agents on droplets.
    
    Uses http_client for automatic retries.
    
    Example:
        client = NodeAgentClient("206.189.122.244", "dop_v1_xxx...")
        
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
    
    def __init__(self, server_ip: str, do_token: str, timeout: int = 30):
        from ..providers import generate_node_agent_key
        
        self.server_ip = server_ip
        self.do_token = do_token
        self.api_key = generate_node_agent_key(do_token)  # Generate internally
        self.timeout = timeout
        self.base_url = f"http://{server_ip}:{self.AGENT_PORT}"
        self._http_client = None
    
    def _get_http_client(self):
        """Get or create async HTTP client."""
        if self._http_client is None:
            from ...http_client import AsyncHttpClient, HttpConfig, RetryConfig
            
            config = HttpConfig(
                timeout=self.timeout,
                retry=RetryConfig(
                    max_retries=2,
                    base_delay=1.0,
                    retry_on_status={500, 502, 503, 504},
                ),
                headers={"X-API-Key": self.api_key},
            )
            self._http_client = AsyncHttpClient(
                config=config,
                base_url=self.base_url,
            )
        return self._http_client
    
    async def __aenter__(self):
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
    
    async def close(self):
        """Close the HTTP client."""
        if self._http_client:
            await self._http_client.close()
            self._http_client = None
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        json_data: Dict = None,
        timeout: int = None,
    ) -> AgentResponse:
        """Make authenticated request to node agent."""
        try:
            client = self._get_http_client()
            response = await client.request(
                method,
                endpoint,
                json=json_data,
                raise_on_error=False,
            )
            
            if response.status_code >= 400:
                return AgentResponse(
                    success=False,
                    data={},
                    error=f"HTTP {response.status_code}: {response.body}",
                )
            
            return AgentResponse(success=True, data=response.json())
                
        except Exception as e:
            error_msg = str(e)
            if "connect" in error_msg.lower():
                return AgentResponse(
                    success=False,
                    data={},
                    error=f"Cannot connect to agent at {self.server_ip}:{self.AGENT_PORT}",
                )
            elif "timeout" in error_msg.lower():
                return AgentResponse(
                    success=False,
                    data={},
                    error=f"Request timed out after {timeout or self.timeout}s",
                )
            return AgentResponse(success=False, data={}, error=error_msg)
    
    # =========================================================================
    # Health
    # =========================================================================
    
    async def ping(self) -> AgentResponse:
        """Check if agent is alive."""
        return await self._request("GET", "/ping")
    
    async def health(self) -> AgentResponse:
        """Get comprehensive health status."""
        return await self._request("GET", "/health")
    
    async def get_metrics(self) -> AgentResponse:
        """Get container and system metrics (CPU, memory, disk)."""
        return await self._request("GET", "/metrics")
    
    async def check_containers_health(self) -> AgentResponse:
        """Health check all running containers on the server."""
        return await self._request("GET", "/health/containers")
    
    async def check_container_health(self, name: str) -> AgentResponse:
        """Health check a specific container."""
        return await self._request("GET", f"/containers/{name}/health")
    
    async def health_tcp(
        self, 
        port: int, 
        host: str = "localhost", 
        timeout: int = 5
    ) -> AgentResponse:
        """
        TCP health check to an internal port on the droplet.
        
        This is used to check if services like Redis, PostgreSQL, etc. are
        accepting connections WITHOUT exposing their ports to the internet.
        
        Args:
            port: Port to check (e.g., 6379 for Redis, 5432 for PostgreSQL)
            host: Host to connect to (default: localhost)
            timeout: Connection timeout in seconds (default: 5)
        
        Returns:
            AgentResponse with:
                - data['status']: 'healthy' or 'unhealthy'
                - data['response_time_ms']: Connection time in ms
                - data['error']: Error message if unhealthy
        """
        return await self._request("POST", "/health/tcp", {
            "port": port,
            "host": host,
            "timeout": timeout,
        })
    
    async def health_http(
        self, 
        port: int, 
        path: str = "/",
        host: str = "localhost", 
        timeout: int = 5,
        method: str = "GET",
    ) -> AgentResponse:
        """
        HTTP health check to an internal port on the droplet.
        
        This is used to check application health endpoints WITHOUT exposing
        their ports to the internet.
        
        Args:
            port: Port to check (e.g., 8000 for Python apps)
            path: HTTP path to check (default: /)
            host: Host to connect to (default: localhost)
            timeout: Request timeout in seconds (default: 5)
            method: HTTP method (default: GET)
        
        Returns:
            AgentResponse with:
                - data['status']: 'healthy' or 'unhealthy'
                - data['status_code']: HTTP status code
                - data['response_time_ms']: Request time in ms
                - data['error']: Error message if unhealthy
        """
        return await self._request("POST", "/health/http", {
            "port": port,
            "path": path,
            "host": host,
            "timeout": timeout,
            "method": method,
        })
    
    async def restart_container(self, name: str) -> AgentResponse:
        """Restart a container."""
        return await self._request("POST", f"/containers/{name}/restart")
    
    # =========================================================================
    # Cron/Scheduler Management
    # =========================================================================
    
    async def list_cron_jobs(self) -> AgentResponse:
        """List all managed cron jobs on the server."""
        return await self._request("GET", "/cron/jobs")
    
    async def remove_cron_job(self, job_id: str) -> AgentResponse:
        """Remove a cron job by ID."""
        return await self._request("POST", "/cron/remove", {"id": job_id})
    
    async def schedule_docker_run(
        self,
        job_id: str,
        schedule: str,
        image: str,
        container_name: str = "",
        env_vars: Dict[str, str] = None,
        volumes: List[str] = None,
        network: str = "",
        command: str = "",
        description: str = "",
    ) -> AgentResponse:
        """
        Schedule a Docker container to run on a cron schedule.
        
        Args:
            job_id: Unique identifier for the job
            schedule: Cron schedule (e.g., "0 2 * * *")
            image: Docker image to run
            container_name: Base container name (timestamp will be appended)
            env_vars: Environment variables
            volumes: Volume mounts (e.g., ["/host/path:/container/path"])
            network: Docker network to use
            command: Command to run in container
            description: Optional description
        """
        return await self._request("POST", "/cron/run-docker", {
            "id": job_id,
            "schedule": schedule,
            "image": image,
            "container_name": container_name,
            "env": env_vars or {},
            "volumes": volumes or [],
            "network": network,
            "command": command,
            "description": description,
        })
    
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
        replace_existing: bool = True,
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
            replace_existing: If True, stop/remove existing container with same name first
        """
        # Stop/remove existing container if requested
        if replace_existing:
            try:
                status = await self.container_status(name)
                if status.success and status.data.get('status') in ('running', 'exited', 'created'):
                    await self.stop_container(name)
                    await self.remove_container(name)
            except:
                pass  # Container doesn't exist, that's fine
        
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
    
    async def start_container(self, name: str) -> AgentResponse:
        """Start a stopped container."""
        return await self._request("POST", f"/containers/{name}/start", timeout=30)
    
    async def remove_container(self, name: str) -> AgentResponse:
        """Remove a container."""
        return await self._request("POST", f"/containers/{name}/remove")
    
    async def container_status(self, name: str) -> AgentResponse:
        """Get container status."""
        return await self._request("GET", f"/containers/{name}/status")
    
    async def inspect_container(self, name: str) -> AgentResponse:
        """Get full container inspection data (for recreating with same config)."""
        return await self._request("GET", f"/containers/{name}/inspect")
    
    async def container_logs(self, name: str, lines: int = 100) -> AgentResponse:
        """Get container logs."""
        return await self._request("GET", f"/containers/{name}/logs?lines={lines}")
    
    async def get_service_logs(
        self,
        workspace_id: str,
        project: str,
        environment: str,
        service: str,
        lines: int = 200,
    ) -> AgentResponse:
        """
        Get logs for a service by identifiers.
        
        Resolves container name (including _secondary) and fetches logs.
        Tries primary first, falls back to secondary if not found.
        
        Args:
            workspace_id: Workspace/user ID
            project: Project name
            environment: Environment (prod, staging, dev)
            service: Service name
            lines: Number of log lines
            
        Returns:
            AgentResponse with logs and resolved container_name in data
        """
        from ..utils.naming import DeploymentNaming
        
        base_name = DeploymentNaming.get_container_name(
            workspace_id=workspace_id,
            project=project,
            env=environment,
            service_name=service,
            secondary=False
        )
        secondary_name = f"{base_name}_secondary"
        
        # Try primary first
        result = await self.container_logs(base_name, lines)
        if result.success:
            result.data['container_name'] = base_name
            return result
        
        # Try secondary
        result = await self.container_logs(secondary_name, lines)
        if result.success:
            result.data['container_name'] = secondary_name
            return result
        
        # Neither found
        return AgentResponse(
            success=False,
            error=f"Container not found. Tried: {base_name}, {secondary_name}",
            data={'container_name': base_name}
        )
    
    async def exec_in_container(
        self, 
        name: str, 
        command: List[str],
        timeout: int = 30,
    ) -> AgentResponse:
        """
        Execute command inside a running container.
        
        Args:
            name: Container name
            command: Command and arguments as list (e.g., ["nginx", "-t"])
            timeout: Request timeout in seconds
            
        Returns:
            AgentResponse with stdout/stderr in data
        """
        return await self._request(
            "POST", 
            f"/containers/{name}/exec",
            {"command": command},
            timeout=timeout,
        )
    
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
    
    async def tag_image(self, source: str, target: str) -> AgentResponse:
        """
        Tag an image for deployment history/rollback.
        
        Args:
            source: Source image (e.g., "myapp:latest" or image ID)
            target: Target tag (e.g., "myapp:deploy_abc123")
        """
        return await self._request(
            "POST", "/images/tag",
            {"source": source, "target": target},
            timeout=30
        )
    
    async def list_images(self, prefix: str = "") -> AgentResponse:
        """
        List Docker images, optionally filtered by prefix.
        
        Args:
            prefix: Filter images by repository prefix
        """
        params = f"?prefix={prefix}" if prefix else ""
        return await self._request("GET", f"/images/list{params}", timeout=30)
    
    async def cleanup_images(
        self, 
        prefix: str, 
        keep: int = 20,
        tag_pattern: str = "deploy_"
    ) -> AgentResponse:
        """
        Cleanup old deployment images, keeping the last N.
        
        Args:
            prefix: Image name prefix (e.g., "abc123_prod_api")
            keep: Number of recent images to keep (default: 20)
            tag_pattern: Tag pattern to match (default: "deploy_")
            
        Returns result with deleted images and any errors.
        """
        return await self._request(
            "POST", "/images/cleanup",
            {"prefix": prefix, "keep": keep, "tag_pattern": tag_pattern},
            timeout=60
        )
    
    async def load_image(self, image_tar: bytes) -> AgentResponse:
        """Load a Docker image from tar file (docker save output).
        
        Uses multipart upload to stream directly to disk on agent (low memory).
        """
        import io
        
        # Use multipart form data - streams to disk, no base64 overhead
        files = {'image_tar': ('image.tar', io.BytesIO(image_tar), 'application/x-tar')}
        
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600.0)) as client:
                response = await client.post(
                    f"{self.base_url}/docker/load",
                    headers={"X-API-Key": self.api_key},
                    files=files,
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return AgentResponse(success=True, data=data)
                else:
                    try:
                        err = response.json().get("error", response.text)
                    except:
                        err = response.text
                    return AgentResponse(success=False, error=err)
                    
        except httpx.TimeoutException:
            return AgentResponse(success=False, error="Request timed out after 600s")
        except Exception as e:
            return AgentResponse(success=False, error=str(e))
    
    async def load_image_stream(self, stream) -> AgentResponse:
        """Load a Docker image from async stream (true streaming).
        
        Args:
            stream: Async iterable yielding bytes chunks
            
        Streams directly to agent without buffering entire file.
        """
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600.0)) as client:
                response = await client.post(
                    f"{self.base_url}/docker/load/stream",
                    headers={
                        "X-API-Key": self.api_key,
                        "Content-Type": "application/octet-stream",
                    },
                    content=stream,
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return AgentResponse(success=True, data=data)
                else:
                    try:
                        err = response.json().get("error", response.text)
                    except:
                        err = response.text
                    return AgentResponse(success=False, error=err)
                    
        except httpx.TimeoutException:
            return AgentResponse(success=False, error="Request timed out after 600s")
        except Exception as e:
            return AgentResponse(success=False, error=str(e))
    
    async def build_image(
        self,
        context_path: str = "/app/",
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
    
    async def get_dockerfile(
        self,
        context_path: str = "/app/",
    ) -> AgentResponse:
        """Get or generate Dockerfile for preview before build."""
        return await self._request(
            "POST", "/docker/dockerfile",
            {"context_path": context_path},
        )
    
    async def upload_tar(
        self,
        tar_data: bytes,
        extract_path: str = "/app/",
    ) -> AgentResponse:
        """Upload and extract tar.gz archive.
        
        Uses multipart upload to stream directly (low memory).
        """
        import io
        
        # Use multipart form data - streams to agent, no base64 overhead
        files = {'tar_file': ('code.tar.gz', io.BytesIO(tar_data), 'application/gzip')}
        data = {'extract_path': extract_path, 'clean': 'true'}
        
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
                response = await client.post(
                    f"{self.base_url}/upload/tar",
                    headers={"X-API-Key": self.api_key},
                    files=files,
                    data=data,
                )
                
                if response.status_code == 200:
                    resp_data = response.json()
                    return AgentResponse(success=True, data=resp_data)
                else:
                    try:
                        err = response.json().get("error", response.text)
                    except:
                        err = response.text
                    return AgentResponse(success=False, error=err)
                    
        except httpx.TimeoutException:
            return AgentResponse(success=False, error="Request timed out after 120s")
        except Exception as e:
            return AgentResponse(success=False, error=str(e))
    
    async def git_clone(
        self,
        repo_url: str,
        branch: str = "main",
        target_path: str = "/app/",
        token: str = None,
        ssh_key: str = None,
    ) -> AgentResponse:
        """Clone a git repository with optional auth.
        
        Args:
            repo_url: Git URL (https:// or git@)
            branch: Branch to clone
            target_path: Where to clone to
            token: GitHub/GitLab personal access token (for HTTPS)
            ssh_key: Private SSH key content (for SSH URLs)
        """
        return await self._request(
            "POST", "/git/clone",
            {
                "url": repo_url,
                "branch": branch,
                "target_path": target_path,
                "token": token,
                "ssh_key": ssh_key,
            },
            timeout=300  # 5 min for large repos
        )
    
    async def docker_login(
        self,
        registry: str,
        username: str,
        password: str,
    ) -> AgentResponse:
        """Login to a Docker registry.
        
        Args:
            registry: Registry URL (e.g., registry.digitalocean.com)
            username: Registry username
            password: Registry password or token
        """
        return await self._request(
            "POST", "/docker/login",
            {
                "registry": registry,
                "username": username,
                "password": password,
            },
            timeout=30
        )
    
    async def pull_image(
        self,
        image: str,
        registry: str = None,
        username: str = None,
        password: str = None,
    ) -> AgentResponse:
        """Pull a Docker image, optionally with registry auth.
        
        Args:
            image: Image to pull (e.g., myregistry.com/myimage:tag)
            registry: Registry URL for auth (optional)
            username: Registry username (optional)
            password: Registry password or token (optional)
        """
        payload = {"image": image}
        if registry:
            payload["registry"] = registry
        if username:
            payload["username"] = username
        if password:
            payload["password"] = password
        
        return await self._request(
            "POST", "/images/pull",
            payload,
            timeout=600  # 10 min for large images
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

    async def deploy_from_stream(
        self,
        name: str,
        image: str,
        stream,
        port: int,
        container_port: int = None,
        env_vars: Dict[str, str] = None,
        remove_existing: bool = True,
        log: callable = None,
    ) -> Dict[str, Any]:
        """
        High-level deploy from image stream.
        
        1. Stop & remove existing container (if exists)
        2. Load image from stream
        3. Start container
        4. Get logs on failure for debugging
        
        Args:
            name: Container name
            image: Image tag (e.g., "myapp:latest")
            stream: Async iterable yielding bytes chunks
            port: Host port to expose
            container_port: Container port (defaults to port)
            env_vars: Environment variables
            remove_existing: Stop/remove existing container first
            log: Optional callback for logging progress
            
        Returns:
            Dict with success, url, error, logs
        """
        if log is None:
            log = lambda msg: None
        
        container_port = container_port or port
        
        # Step 1: Remove existing container
        if remove_existing:
            try:
                status = await self.container_status(name)
                if status.success and status.data.get('status') in ('running', 'exited', 'created'):
                    log(f"Stopping existing container for update...")
                    await self.stop_container(name)
                    await self.remove_container(name)
            except:
                pass  # Container doesn't exist, that's fine
        
        # Step 2: Load image from stream
        load_result = await self.load_image_stream(stream)
        if not load_result.success:
            return {
                "success": False,
                "error": f"Image load failed: {load_result.error}",
            }
        
        # Step 3: Run container
        run_result = await self.run_container(
            name=name,
            image=image,
            ports={str(port): str(container_port)},
            env_vars=env_vars or {},
        )
        
        if run_result.success:
            return {
                "success": True,
                "url": f"http://{self.server_ip}:{port}",
                "container_id": run_result.data.get("container_id"),
            }
        
        # Step 4: Get logs on failure
        error_logs = None
        try:
            logs_result = await self.container_logs(name, lines=50)
            if logs_result.success and logs_result.data.get('logs'):
                error_logs = logs_result.data['logs']
        except:
            pass
        
        return {
            "success": False,
            "error": run_result.error,
            "logs": error_logs,
        }

    # =========================================================================
    # File & Directory Management
    # =========================================================================
    
    async def create_directory(self, path: str, mode: str = "755") -> AgentResponse:
        """
        Create a directory on the server.
        
        Args:
            path: Directory path to create
            mode: Permission mode (default: 755)
            
        Returns:
            AgentResponse
        """
        return await self._request(
            "POST", 
            "/files/mkdir",
            {"path": path, "mode": mode},
        )
    
    async def read_file(self, path: str) -> AgentResponse:
        """
        Read a file from the server.
        
        Args:
            path: File path
            
        Returns:
            AgentResponse with data["content"]
        """
        return await self._request("GET", f"/files/read?path={path}")
    
    async def file_exists(self, path: str) -> bool:
        """Check if a file exists."""
        result = await self._request("GET", f"/files/exists?path={path}")
        return result.success and result.data.get("exists", False)
    
    async def delete_file(self, path: str) -> AgentResponse:
        """Delete a file."""
        return await self._request("POST", "/files/delete", {"path": path})
    
    # =========================================================================
    # Nginx Management
    # =========================================================================
    
    async def ensure_nginx_running(
        self,
        nginx_conf_path: str = "/local/nginx/nginx.conf",
        conf_d_path: str = "/local/nginx/conf.d",
        stream_d_path: str = "/local/nginx/stream.d",
        certs_path: str = "/local/nginx/certs",
        logs_path: str = "/local/nginx/logs",
    ) -> AgentResponse:
        """
        Ensure nginx container is running with proper config mounts.
        Creates config directories and starts nginx if not running.
        Also opens firewall ports 80 and 443.
        """
        # Create directories
        for path in [conf_d_path, stream_d_path, certs_path, logs_path]:
            await self.create_directory(path)
        
        # Open firewall ports for HTTP/HTTPS
        await self.firewall_allow(80, "tcp")
        await self.firewall_allow(443, "tcp")
        
        # Always write nginx.conf to ensure latest settings
        default_conf = self._get_default_nginx_conf()
        await self.write_file(nginx_conf_path, default_conf)
        
        # Check if nginx is running
        status = await self.container_status("nginx")
        if status.success:
            container_state = status.data.get("status", "").lower()
            if container_state == "running":
                await self.reload_nginx()
                return AgentResponse(success=True, data={"status": "already_running"})
            elif container_state in ("exited", "stopped", "dead", "created"):
                await self.remove_container("nginx")
        
        # Start nginx container with host network
        result = await self.run_container(
            name="nginx",
            image="nginx:alpine",
            network="host",
            volumes=[
                f"{nginx_conf_path}:/etc/nginx/nginx.conf:ro",
                f"{conf_d_path}:/etc/nginx/conf.d:ro",
                f"{stream_d_path}:/etc/nginx/stream.d:ro",
                f"{certs_path}:/etc/nginx/certs:ro",
                f"{logs_path}:/var/log/nginx",
            ],
            restart_policy="unless-stopped",
            replace_existing=True,
        )
        
        return result
    
    async def test_nginx_config(self) -> AgentResponse:
        """
        Test nginx configuration for errors.
        Uses the /nginx/test endpoint which works with both Docker and systemctl nginx.
        
        Returns:
            AgentResponse with success=True if config is valid
        """
        result = await self._request("GET", "/nginx/test")
        if result.success:
            # Check the 'valid' field in the response
            is_valid = result.data.get("valid", False)
            if not is_valid:
                return AgentResponse(
                    success=False,
                    error=result.data.get("output", "Invalid nginx configuration"),
                    data=result.data,
                )
        return result
    
    async def write_nginx_config(
        self,
        config_name: str,
        content: str,
        config_type: str = "http",  # "http" or "stream"
        reload: bool = True,
    ) -> AgentResponse:
        """
        Write an nginx config file and optionally reload.
        
        Args:
            config_name: Config filename (without .conf extension)
            content: Config content
            config_type: "http" for HTTP configs, "stream" for TCP configs
            reload: Reload nginx after writing
            
        Returns:
            AgentResponse
        """
        if config_type == "stream":
            path = f"/local/nginx/stream.d/{config_name}.conf"
        else:
            path = f"/local/nginx/conf.d/{config_name}.conf"
        
        # Ensure directory exists
        await self.create_directory(f"/local/nginx/{config_type == 'stream' and 'stream.d' or 'conf.d'}")
        
        # Write config
        result = await self.write_file(path, content)
        if not result.success:
            return result
        
        # Reload nginx
        if reload:
            # Test config first
            test_result = await self.test_nginx_config()
            if not test_result.success:
                # Config invalid - delete the file
                await self.delete_file(path)
                return AgentResponse(
                    success=False,
                    error=f"Invalid nginx config: {test_result.error}",
                )
            
            return await self.reload_nginx()
        
        return result
    
    async def remove_nginx_config(
        self,
        config_name: str,
        config_type: str = "http",
        reload: bool = True,
    ) -> AgentResponse:
        """
        Remove an nginx config file and reload.
        
        Args:
            config_name: Config filename (without .conf extension)
            config_type: "http" or "stream"
            reload: Reload nginx after removing
            
        Returns:
            AgentResponse
        """
        if config_type == "stream":
            path = f"/local/nginx/stream.d/{config_name}.conf"
        else:
            path = f"/local/nginx/conf.d/{config_name}.conf"
        
        result = await self.delete_file(path)
        
        if reload and result.success:
            await self.reload_nginx()
        
        return result
    
    def _get_default_nginx_conf(self) -> str:
        """Get default nginx.conf content."""
        return '''# Auto-generated nginx.conf
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {
    worker_connections 4096;
    use epoll;
    multi_accept on;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    # Hash bucket sizes for long server names (e.g., 6c007a-ai-prod-ai-agents.digitalpixo.com)
    server_names_hash_bucket_size 128;
    server_names_hash_max_size 1024;

    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for"';

    access_log /var/log/nginx/access.log main;

    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    client_max_body_size 100M;

    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css text/xml application/json application/javascript 
               application/xml application/xml+rss text/javascript;

    limit_req_zone $binary_remote_addr zone=api:10m rate=100r/s;

    include /etc/nginx/conf.d/*.conf;
}

stream {
    include /etc/nginx/stream.d/*.conf;
}
'''
    
    # =========================================================================
    # Docker Network Management
    # =========================================================================
    
    async def create_network(self, name: str) -> AgentResponse:
        """Create a Docker network."""
        return await self._request("POST", "/networks/create", {"name": name})
    
    async def network_exists(self, name: str) -> bool:
        """Check if a Docker network exists."""
        result = await self._request("GET", f"/networks/{name}")
        return result.success
    
    async def ensure_network(self, name: str) -> AgentResponse:
        """Create network if it doesn't exist."""
        if await self.network_exists(name):
            return AgentResponse(success=True, data={"status": "exists"})
        return await self.create_network(name)
