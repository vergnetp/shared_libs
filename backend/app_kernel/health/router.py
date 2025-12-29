"""
Health check router.

Endpoints:
- GET /healthz: Liveness probe (always 200 if app is running)
- GET /readyz: Readiness probe (checks all configured systems)

No authentication required for health checks.
"""

import asyncio
from typing import List, Tuple, Callable, Awaitable, Optional
from fastapi import APIRouter, Response
from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Health check response."""
    status: str  # "healthy" or "unhealthy"
    checks: dict = {}


class LivenessResponse(BaseModel):
    """Simple liveness response."""
    status: str = "ok"


# Type alias for health check functions
HealthCheckFn = Callable[[], Awaitable[Tuple[bool, str]]]


def create_health_router(
    health_checks: Tuple[HealthCheckFn, ...] = (),
    health_path: str = "/healthz",
    ready_path: str = "/readyz",
) -> APIRouter:
    """
    Create health check router.
    
    Args:
        health_checks: Tuple of async functions, each returning (healthy: bool, message: str)
        health_path: Path for liveness endpoint
        ready_path: Path for readiness endpoint
        
    Returns:
        FastAPI router with health endpoints
    """
    router = APIRouter(tags=["Health"])
    
    @router.get(
        health_path,
        response_model=LivenessResponse,
        summary="Liveness probe",
        description="Returns 200 if the application is running. Used by load balancers and orchestrators.",
    )
    async def liveness():
        """
        Simple liveness check.
        
        Always returns 200 if the app is running.
        Use this for Kubernetes liveness probes.
        """
        return LivenessResponse(status="ok")
    
    @router.get(
        ready_path,
        response_model=HealthResponse,
        summary="Readiness probe",
        description="Returns 200 if all dependencies are healthy, 503 otherwise.",
    )
    async def readiness(response: Response):
        """
        Readiness check.
        
        Runs all configured health checks and returns:
        - 200 if all checks pass
        - 503 if any check fails
        
        Use this for Kubernetes readiness probes and load balancer health checks.
        """
        if not health_checks:
            return HealthResponse(
                status="healthy",
                checks={"note": "no health checks configured"},
            )
        
        # Run all checks concurrently
        results = await asyncio.gather(
            *[check() for check in health_checks],
            return_exceptions=True,
        )
        
        checks = {}
        all_healthy = True
        
        for i, result in enumerate(results):
            check_name = getattr(health_checks[i], "__name__", f"check_{i}")
            
            if isinstance(result, Exception):
                checks[check_name] = {
                    "healthy": False,
                    "message": f"check failed: {result}",
                }
                all_healthy = False
            else:
                healthy, message = result
                checks[check_name] = {
                    "healthy": healthy,
                    "message": message,
                }
                if not healthy:
                    all_healthy = False
        
        if not all_healthy:
            response.status_code = 503
        
        return HealthResponse(
            status="healthy" if all_healthy else "unhealthy",
            checks=checks,
        )
    
    return router


# =============================================================================
# Built-in health check functions
# =============================================================================

async def check_redis(redis_url: str = None) -> Tuple[bool, str]:
    """
    Health check for Redis connection.
    
    Usage:
        from functools import partial
        check = partial(check_redis, redis_url="redis://localhost:6379")
        settings = KernelSettings(health_checks=(check,))
    """
    if not redis_url:
        return False, "redis_url not configured"
    
    try:
        import redis.asyncio as redis
        client = redis.from_url(redis_url)
        await client.ping()
        await client.close()
        return True, "redis connected"
    except ImportError:
        return False, "redis package not installed"
    except Exception as e:
        return False, f"redis error: {e}"


async def check_database(db_manager) -> Tuple[bool, str]:
    """
    Health check for database connection.
    
    Usage:
        from functools import partial
        check = partial(check_database, db_manager=get_db_manager())
        settings = KernelSettings(health_checks=(check,))
    """
    if db_manager is None:
        return False, "database manager not configured"
    
    try:
        async with db_manager as conn:
            # Simple query to verify connection
            await conn.execute("SELECT 1")
        return True, "database connected"
    except Exception as e:
        return False, f"database error: {e}"
