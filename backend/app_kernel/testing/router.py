"""
Test router factory (internal).

Creates POST /test/{name} endpoints from a list of runner functions.
Endpoint names derived from function names: run_functional_tests → POST /test/functional-tests.

Each endpoint gets: admin gate, bearer token extraction, base_url detection,
SSE StreamingResponse wrapping, and cancel support via TaskStream.

Runner fn signature:
    async def run_functional_tests(base_url: str, auth_token: str) -> AsyncIterator[str]:
        ...
"""

import os
from typing import AsyncIterator, Callable, List

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..auth import get_current_user, UserIdentity


_bearer_scheme = HTTPBearer(auto_error=False)


def _detect_base_url(request: Request, api_prefix: str) -> str:
    """
    Build API base URL from the incoming request + known api_prefix.
    
    E.g. request to http://localhost:8000/test/functional-tests
         with api_prefix="/api/v1"
         → http://localhost:8000/api/v1
    """
    override = os.environ.get("API_BASE_URL")
    if override:
        return override.rstrip("/")
    
    base = str(request.base_url).rstrip("/")
    return f"{base}{api_prefix}"


def _slug_from_fn(fn: Callable) -> str:
    """Derive URL slug from function name.
    
    run_functional_tests → functional-tests
    run_smoke            → smoke
    my_tests             → my-tests
    """
    name = fn.__name__
    if name.startswith("run_"):
        name = name[4:]
    return name.replace("_", "-")


def _create_test_router(runners: List[Callable], api_prefix: str = "/api/v1") -> APIRouter:
    """
    Build a router with one POST /test/{slug} per runner function.
    
    Each endpoint: admin-only, extracts base_url + auth_token,
    wraps the runner's SSE stream in a StreamingResponse.
    
    Args:
        runners: List of async generator functions.
        api_prefix: API prefix for building base_url (e.g. "/api/v1").
    """
    router = APIRouter(tags=["testing"], prefix="/test")
    
    for fn in runners:
        slug = _slug_from_fn(fn)
        
        # Closure needs its own fn reference
        def _make_endpoint(runner_fn: Callable, endpoint_slug: str):
            
            @router.post(
                f"/{endpoint_slug}",
                summary=f"Run {endpoint_slug.replace('-', ' ')} tests",
                description=(
                    f"Run {endpoint_slug.replace('-', ' ')} against the running API. "
                    "Streams SSE events with progress logs and emits a final report. "
                    "Cancellable via POST /tasks/{{task_id}}/cancel."
                ),
            )
            async def run_test(
                request: Request,
                user: UserIdentity = Depends(get_current_user),
                credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
            ):
                if user.role != "admin":
                    raise HTTPException(403, "Test endpoints require admin role")
                
                base_url = _detect_base_url(request, api_prefix)
                auth_token = credentials.credentials
                
                async def stream():
                    async for event in runner_fn(
                        base_url=base_url,
                        auth_token=auth_token,
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
        
        _make_endpoint(fn, slug)
    
    return router
