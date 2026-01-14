"""
Health check endpoints.

Provides:
- /healthz: Simple liveness check (always 200 if app is running)
- /readyz: Readiness check (checks all configured health checks)

Usage:
    # In KernelSettings, pass health check functions:
    settings = KernelSettings(
        health_checks=(check_db, check_redis),
    )
    
    # Each check function signature:
    async def check_db() -> Tuple[bool, str]:
        try:
            await db.execute("SELECT 1")
            return True, "database connected"
        except Exception as e:
            return False, f"database error: {e}"
"""

from .router import create_health_router

__all__ = ["create_health_router"]
