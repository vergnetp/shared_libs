"""
Application dependencies.

This file is YOUR code - never overwritten by generator.
Add custom dependencies here.
"""

from typing import AsyncGenerator
from backend.app_kernel.db import db_session_dependency, get_db_session

# Re-export kernel's db dependency
get_db = db_session_dependency

# For workers - use context manager
get_db_context = get_db_session


# =============================================================================
# Add your custom dependencies below
# =============================================================================

# Example:
# _my_service: Optional[MyService] = None
#
# def get_my_service() -> MyService:
#     if _my_service is None:
#         raise RuntimeError("Service not initialized")
#     return _my_service
