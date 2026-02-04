"""
Test API client — authenticated HTTP client with SSE stream consumption.

Provides base HTTP methods (get, post, put, patch, delete) and SSE-aware
stream methods (stream_post, stream_get, stream_delete, stream_upload)
that consume SSE events, propagate cancellation, and return structured results.

Apps subclass to add domain-specific methods:

    class DeployApiClient(TestApiClient):
        async def deploy_zip(self, project, service, zip_bytes, **kw):
            body = {"project_name": project, ...}
            return await self.stream_post("/deployments", json=body)
        
        async def get_snapshots(self):
            return await self.get("/snapshots")
"""

import json
import asyncio
from typing import Any, Callable, Dict, Optional

import httpx

from ..tasks import cancel
from ..tasks.cancel import Cancelled


# ---------------------------------------------------------------------------
# SSE consumer
# ---------------------------------------------------------------------------

async def consume_sse(
    response: httpx.Response,
    outer_task_id: str = None,
    on_log: Callable[[str, str], None] = None,
) -> Dict[str, Any]:
    """
    Consume an SSE stream from an httpx response.
    
    Handles kernel-standard events (task_id, log, complete) and captures
    any app-specific events generically into the result dict.
    
    If outer_task_id is provided, monitors for cancellation and propagates
    it to the inner task (same process, in-memory cancel Events).
    
    Args:
        response: httpx streaming response to consume.
        outer_task_id: TaskStream ID for cancel propagation.
        on_log: Optional callback(message, level) invoked for each log event.
            Useful for forwarding progress to an outer stream.
    
    Returns:
        Dict with at minimum {"success": bool, "_logs": list}.
        The 'complete' event data is merged in. Any other events are
        captured as result[event_name] = data.
    
    Raises:
        Cancelled: if the outer task was cancelled during consumption.
    """
    result: Dict[str, Any] = {"success": False}
    current_event: Optional[str] = None
    log_lines = []
    inner_task_id: Optional[str] = None
    
    async def _cancel_watcher():
        """Poll outer cancel state and propagate to inner task."""
        nonlocal inner_task_id
        while True:
            await asyncio.sleep(0.5)
            if cancel.is_cancelled(outer_task_id):
                if inner_task_id:
                    cancel.trigger(inner_task_id)
                return
    
    watcher = None
    if outer_task_id:
        watcher = asyncio.create_task(_cancel_watcher())
    
    try:
        async for line in response.aiter_lines():
            line = line.strip()
            if not line:
                current_event = None
                continue
            
            if line.startswith("event: "):
                current_event = line[7:]
            elif line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                
                # --- Kernel-standard events (special handling) ---
                
                if current_event == "complete":
                    # Merge complete payload into result
                    result.update(data)
                    
                elif current_event == "task_id":
                    # Track inner task_id for cancel propagation
                    inner_task_id = data.get("task_id")
                    result["_inner_task_id"] = inner_task_id
                    
                elif current_event == "log":
                    msg = data.get("message", "")
                    level = data.get("level", "info")
                    log_lines.append(msg)
                    if on_log:
                        on_log(msg, level)
                
                # --- Any other event: capture generically ---
                else:
                    result[current_event] = data
                    
    finally:
        if watcher and not watcher.done():
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
    
    result["_logs"] = log_lines
    
    # After stream ends, check if we were the reason it ended
    if outer_task_id and cancel.is_cancelled(outer_task_id):
        raise Cancelled(f"Task {outer_task_id} cancelled")
    
    return result


# ---------------------------------------------------------------------------
# Base test client
# ---------------------------------------------------------------------------

class TestApiClient:
    """
    Authenticated HTTP client for self-testing via own API routes.
    
    Provides generic HTTP methods and SSE-consuming stream methods.
    Subclass to add domain-specific convenience methods.
    
    Args:
        base_url: API base URL (e.g. "http://localhost:8000/api/v1")
        auth_token: Bearer token for authentication
        outer_task_id: TaskStream ID for cancel propagation
        timeout: Request timeout in seconds (default 600)
    """
    
    def __init__(
        self,
        base_url: str,
        auth_token: str,
        outer_task_id: str = None,
        timeout: float = 600.0,
    ):
        self.base_url = base_url.rstrip('/')
        self.auth_token = auth_token
        self.outer_task_id = outer_task_id
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
        }
    
    def _url(self, path: str) -> str:
        """Build full URL from path."""
        return f"{self.base_url}{path}" if path.startswith('/') else f"{self.base_url}/{path}"
    
    @staticmethod
    def _strip_none(d: Dict) -> Dict:
        """Remove None values — Pydantic v2 rejects null for non-Optional types."""
        return {k: v for k, v in d.items() if v is not None}
    
    # --- Standard HTTP ---
    
    async def get(self, path: str, params: Dict = None, timeout: float = None) -> Any:
        """GET request, returns parsed JSON."""
        async with httpx.AsyncClient(timeout=timeout or self.timeout) as client:
            resp = await client.get(self._url(path), headers=self.headers, params=params)
            resp.raise_for_status()
            return resp.json()
    
    async def post(self, path: str, json: Dict = None, **kwargs) -> Any:
        """POST request (non-streaming), returns parsed JSON."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(self._url(path), json=json, headers=self.headers, **kwargs)
            resp.raise_for_status()
            return resp.json()
    
    async def put(self, path: str, json: Dict = None, **kwargs) -> Any:
        """PUT request, returns parsed JSON."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.put(self._url(path), json=json, headers=self.headers, **kwargs)
            resp.raise_for_status()
            return resp.json()
    
    async def patch(self, path: str, json: Dict = None, **kwargs) -> Any:
        """PATCH request, returns parsed JSON."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.patch(self._url(path), json=json, headers=self.headers, **kwargs)
            resp.raise_for_status()
            return resp.json()
    
    async def delete(self, path: str, params: Dict = None, timeout: float = None) -> Any:
        """DELETE request (non-streaming), returns parsed JSON."""
        async with httpx.AsyncClient(timeout=timeout or self.timeout) as client:
            resp = await client.delete(self._url(path), headers=self.headers, params=params)
            resp.raise_for_status()
            return resp.json()
    
    # --- SSE-streaming HTTP ---
    
    async def _stream_request(
        self,
        method: str,
        path: str,
        json: Dict = None,
        files: Dict = None,
        data: Dict = None,
        headers: Dict = None,
        timeout: float = None,
        on_log: Callable[[str, str], None] = None,
    ) -> Dict[str, Any]:
        """
        Generic SSE-consuming request.
        
        Used by stream_post, stream_get, stream_upload, stream_delete.
        Returns structured dict with success, _logs, and any event-
        specific fields captured from the SSE stream.
        """
        req_headers = headers or self.headers
        req_timeout = timeout or self.timeout
        
        async with httpx.AsyncClient(timeout=req_timeout) as client:
            async with client.stream(
                method, self._url(path),
                json=json, files=files, data=data, headers=req_headers,
            ) as resp:
                if resp.status_code != 200:
                    text = await resp.aread()
                    return {"success": False, "error": f"HTTP {resp.status_code}: {text.decode()}"}
                return await consume_sse(resp, self.outer_task_id, on_log=on_log)
    
    async def stream_post(self, path: str, json: Dict = None, **kwargs) -> Dict[str, Any]:
        """POST that consumes an SSE response stream."""
        return await self._stream_request("POST", path, json=json, **kwargs)
    
    async def stream_get(self, path: str, **kwargs) -> Dict[str, Any]:
        """GET that consumes an SSE response stream."""
        return await self._stream_request("GET", path, **kwargs)
    
    async def stream_delete(self, path: str, **kwargs) -> Dict[str, Any]:
        """DELETE that consumes an SSE response stream."""
        return await self._stream_request("DELETE", path, **kwargs)
    
    async def stream_upload(
        self,
        path: str,
        files: Dict,
        data: Dict = None,
        timeout: float = None,
        on_log: Callable[[str, str], None] = None,
    ) -> Dict[str, Any]:
        """
        Multipart upload POST that consumes an SSE response stream.
        
        Args:
            files: httpx-compatible files dict (e.g. {"file": (name, bytes, mime)})
            data: Form data fields
        """
        # Multipart — don't send Content-Type header (httpx sets boundary)
        headers = {"Authorization": f"Bearer {self.auth_token}"}
        return await self._stream_request(
            "POST", path, files=files, data=data,
            headers=headers, timeout=timeout, on_log=on_log,
        )
