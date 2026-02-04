"""
Test router factory.

Creates the /test/functional endpoint with admin gating, base_url detection,
and StreamingResponse wrapping. Apps just provide the runner function.

Usage:
    from app_kernel.testing import create_test_router
    from .my_tests import run_functional_tests

    router = create_test_router(
        runner_fn=run_functional_tests,
        required_env=["DO_TOKEN", "CF_TOKEN"],
    )

The runner_fn signature must be:

    async def run_functional_tests(
        base_url: str,
        auth_token: str,
        **kwargs,
    ) -> AsyncIterator[str]:
        ...
"""

import os
from typing import AsyncIterator, Callable, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..auth import get_current_user, UserIdentity


_bearer_scheme = HTTPBearer(auto_error=False)


def _detect_base_url(request: Request) -> str:
    """
    Build API base URL from the incoming request.
    
    Detects the API prefix by finding where /test/ starts in the path.
    E.g. /api/v1/test/functional → base = http://host:port/api/v1
    """
    override = os.environ.get("API_BASE_URL")
    if override:
        return override.rstrip("/")
    
    base = str(request.base_url).rstrip("/")
    path = request.url.path
    
    test_idx = path.find("/test/")
    if test_idx > 0:
        api_prefix = path[:test_idx]
        return f"{base}{api_prefix}"
    
    return base


def create_test_router(
    runner_fn: Callable[..., AsyncIterator[str]],
    required_env: List[str] = None,
    prefix: str = "/test",
    require_admin: bool = True,
    summary: str = "Run functional tests",
    description: str = None,
    extra_kwargs_fn: Callable[[Request], dict] = None,
) -> APIRouter:
    """
    Create a test router that mounts POST {prefix}/functional.
    
    Args:
        runner_fn: Async generator that yields SSE events.
            Signature: (base_url: str, auth_token: str, **kwargs) -> AsyncIterator[str]
        required_env: Environment variables that must be set (checked before streaming).
        prefix: URL prefix (default "/test").
        require_admin: If True, require admin role (default True).
        summary: OpenAPI summary for the endpoint.
        description: OpenAPI description.
        extra_kwargs_fn: Optional function(request) -> dict of extra kwargs
            passed to runner_fn. Useful for app-specific context like services_path.
    
    Returns:
        FastAPI APIRouter ready to include in app.
    """
    router = APIRouter(tags=["testing"], prefix=prefix)
    required_env = required_env or []
    
    if description is None:
        description = (
            "Run functional tests against the running API. "
            "Streams SSE events with progress logs and emits a final report. "
            "Cancellable via POST /tasks/{task_id}/cancel."
        )
    
    @router.post("/functional", summary=summary, description=description)
    async def run_functional_test(
        request: Request,
        user: UserIdentity = Depends(get_current_user),
        credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    ):
        # Admin gate
        if require_admin and user.role != "admin":
            raise HTTPException(403, "Functional tests require admin role")
        
        # Validate required env vars
        for env_name in required_env:
            if not os.environ.get(env_name):
                raise HTTPException(500, f"{env_name} not configured")
        
        base_url = _detect_base_url(request)
        auth_token = credentials.credentials
        
        # Build kwargs
        kwargs = {}
        if extra_kwargs_fn:
            kwargs = extra_kwargs_fn(request)
        
        async def stream():
            async for event in runner_fn(
                base_url=base_url,
                auth_token=auth_token,
                **kwargs,
            ):
                yield event
        
        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    
    return router
