"""Feature flags storage and evaluation."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_flags_schema(db) -> None:
    """Create feature flags table."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS feature_flags (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            enabled INTEGER DEFAULT 0,
            rollout_percent INTEGER DEFAULT 100,
            allowed_workspaces TEXT,
            allowed_users TEXT,
            metadata TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_flags_name ON feature_flags(name)")


async def set_flag(
    db,
    name: str,
    enabled: bool = True,
    description: Optional[str] = None,
    rollout_percent: int = 100,
    workspaces: Optional[List[str]] = None,
    users: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create or update a feature flag.
    
    Args:
        name: Flag name (e.g., "new_dashboard", "beta_feature")
        enabled: Global enable/disable
        description: Human-readable description
        rollout_percent: Percentage of users to enable for (0-100)
        workspaces: List of workspace IDs to enable for (overrides rollout)
        users: List of user IDs to enable for (overrides rollout)
        metadata: Additional data
    """
    import uuid
    
    now = _now_iso()
    
    # Check if exists
    existing = await get_flag(db, name)
    
    flag_data = {
        "name": name,
        "description": description,
        "enabled": 1 if enabled else 0,
        "rollout_percent": max(0, min(100, rollout_percent)),
        "allowed_workspaces": json.dumps(workspaces) if workspaces else None,
        "allowed_users": json.dumps(users) if users else None,
        "metadata": json.dumps(metadata) if metadata else None,
        "updated_at": now,
    }
    
    if existing:
        flag_data["id"] = existing["id"]
    else:
        flag_data["id"] = str(uuid.uuid4())
        flag_data["created_at"] = now
    
    await db.save_entity("feature_flags", flag_data)
    
    return await get_flag(db, name)


async def get_flag(db, name: str) -> Optional[Dict[str, Any]]:
    """Get a feature flag by name."""
    results = await db.find_entities(
        "feature_flags",
        where_clause="[name] = ?",
        params=(name,),
        limit=1,
    )
    
    if not results:
        return None
    
    row = results[0]
    return _parse_flag(row)


async def list_flags(db) -> List[Dict[str, Any]]:
    """List all feature flags."""
    results = await db.find_entities(
        "feature_flags",
        order_by="[name] ASC",
    )
    
    return [_parse_flag(row) for row in results]


async def delete_flag(db, name: str) -> bool:
    """Delete a feature flag."""
    flag = await get_flag(db, name)
    if not flag:
        return False
    
    await db.delete_entity("feature_flags", flag["id"], permanent=True)
    return True


def _parse_flag(row: Dict[str, Any]) -> Dict[str, Any]:
    """Parse a flag row from database."""
    flag = {
        "id": row["id"],
        "name": row["name"],
        "description": row.get("description"),
        "enabled": bool(row.get("enabled")),
        "rollout_percent": row.get("rollout_percent", 100),
        "allowed_workspaces": [],
        "allowed_users": [],
        "metadata": {},
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }
    
    if row.get("allowed_workspaces"):
        try:
            flag["allowed_workspaces"] = json.loads(row["allowed_workspaces"])
        except:
            pass
    
    if row.get("allowed_users"):
        try:
            flag["allowed_users"] = json.loads(row["allowed_users"])
        except:
            pass
    
    if row.get("metadata"):
        try:
            flag["metadata"] = json.loads(row["metadata"])
        except:
            pass
    
    return flag


async def flag_enabled(
    db,
    name: str,
    user_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> bool:
    """
    Check if a feature flag is enabled for a user/workspace.
    
    Evaluation order:
    1. If flag doesn't exist → False
    2. If globally disabled → False
    3. If user in allowed_users → True
    4. If workspace in allowed_workspaces → True
    5. Check rollout percentage (consistent per user)
    """
    flag = await get_flag(db, name)
    
    # Flag doesn't exist
    if not flag:
        return False
    
    # Globally disabled
    if not flag["enabled"]:
        return False
    
    # Check explicit user allowlist
    if user_id and flag["allowed_users"]:
        if user_id in flag["allowed_users"]:
            return True
    
    # Check explicit workspace allowlist
    if workspace_id and flag["allowed_workspaces"]:
        if workspace_id in flag["allowed_workspaces"]:
            return True
    
    # If allowlists exist but user/workspace not in them, check rollout
    rollout = flag["rollout_percent"]
    
    # 100% rollout = everyone
    if rollout >= 100:
        return True
    
    # 0% rollout = no one (unless in allowlist)
    if rollout <= 0:
        return False
    
    # Consistent rollout based on user_id or workspace_id
    identifier = user_id or workspace_id or "anonymous"
    return _in_rollout(name, identifier, rollout)


def _in_rollout(flag_name: str, identifier: str, percent: int) -> bool:
    """
    Determine if identifier is in rollout percentage.
    Uses consistent hashing so same user always gets same result.
    """
    # Hash flag name + identifier for consistent bucketing
    hash_input = f"{flag_name}:{identifier}".encode()
    hash_value = int(hashlib.md5(hash_input).hexdigest(), 16)
    
    # Map to 0-99
    bucket = hash_value % 100
    
    return bucket < percent
