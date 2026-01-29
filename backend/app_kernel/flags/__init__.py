"""
Feature Flags - Toggle features without deploy.

Usage:
    # Check flag in code
    if await flag_enabled(db, "new_dashboard", user_id=user.id):
        return new_dashboard()
    else:
        return old_dashboard()
    
    # Check with workspace
    if await flag_enabled(db, "beta_feature", workspace_id=workspace_id):
        ...
    
    # Admin: Set flag
    await set_flag(db, "new_dashboard",
        enabled=True,
        rollout_percent=10,           # 10% of users
        workspaces=["ws-123"],        # Specific workspaces
        users=["user-456"],           # Specific users
    )
    
    # Admin: Get all flags
    flags = await list_flags(db)
"""

from .stores import (
    flag_enabled,
    get_flag,
    set_flag,
    list_flags,
    delete_flag,
    init_flags_schema,
)
from .router import create_flags_router

__all__ = [
    "flag_enabled",
    "get_flag",
    "set_flag",
    "list_flags",
    "delete_flag",
    "init_flags_schema",
    "create_flags_router",
]
