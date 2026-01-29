"""API key storage and operations."""

import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any


def _generate_key(prefix: str = "sk_live") -> tuple[str, str]:
    """Generate API key and its hash. Returns (plaintext, hash)."""
    raw = secrets.token_urlsafe(32)
    plaintext = f"{prefix}_{raw}"
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    return plaintext, key_hash


def _hash_key(key: str) -> str:
    """Hash an API key for lookup."""
    return hashlib.sha256(key.encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_api_keys_schema(db) -> None:
    """Create API keys table."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            workspace_id TEXT,
            name TEXT NOT NULL,
            key_hash TEXT NOT NULL UNIQUE,
            key_prefix TEXT,
            scopes TEXT,
            expires_at TEXT,
            last_used_at TEXT,
            revoked_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_workspace ON api_keys(workspace_id)")


async def create_api_key(
    db,
    user_id: str,
    workspace_id: Optional[str] = None,
    name: str = "API Key",
    scopes: Optional[List[str]] = None,
    expires_in_days: Optional[int] = None,
    prefix: str = "sk_live",
) -> Dict[str, Any]:
    """
    Create a new API key.
    
    Returns dict with plaintext key (only returned once, never stored).
    """
    import json
    import uuid
    
    key_id = str(uuid.uuid4())
    plaintext, key_hash = _generate_key(prefix)
    now = _now_iso()
    
    expires_at = None
    if expires_in_days:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).isoformat()
    
    await db.save_entity("api_keys", {
        "id": key_id,
        "user_id": user_id,
        "workspace_id": workspace_id,
        "name": name,
        "key_hash": key_hash,
        "key_prefix": plaintext[:12] + "...",  # For display: "sk_live_a1b2..."
        "scopes": json.dumps(scopes or []),
        "expires_at": expires_at,
        "last_used_at": None,
        "revoked_at": None,
        "created_at": now,
        "updated_at": now,
    })
    
    return {
        "id": key_id,
        "key": plaintext,  # Only time plaintext is returned!
        "name": name,
        "scopes": scopes or [],
        "expires_at": expires_at,
        "created_at": now,
    }


async def verify_api_key(db, key: str) -> Optional[Dict[str, Any]]:
    """
    Verify an API key and return its data if valid.
    Updates last_used_at on successful verification.
    """
    import json
    
    key_hash = _hash_key(key)
    
    results = await db.find_entities(
        "api_keys",
        where_clause="[key_hash] = ?",
        params=(key_hash,),
        limit=1,
    )
    
    if not results:
        return None
    
    key_data = results[0]
    
    # Check if revoked
    if key_data.get("revoked_at"):
        return None
    
    # Check if expired
    if key_data.get("expires_at"):
        expires = datetime.fromisoformat(key_data["expires_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > expires:
            return None
    
    # Update last_used_at
    await db.save_entity("api_keys", {
        "id": key_data["id"],
        "last_used_at": _now_iso(),
    })
    
    # Parse scopes
    scopes = []
    if key_data.get("scopes"):
        try:
            scopes = json.loads(key_data["scopes"])
        except:
            pass
    
    return {
        "id": key_data["id"],
        "user_id": key_data["user_id"],
        "workspace_id": key_data.get("workspace_id"),
        "name": key_data["name"],
        "scopes": scopes,
    }


async def list_api_keys(
    db,
    user_id: str,
    workspace_id: Optional[str] = None,
    include_revoked: bool = False,
) -> List[Dict[str, Any]]:
    """List API keys for a user (never returns plaintext or hash)."""
    import json
    
    where = "[user_id] = ?"
    params = [user_id]
    
    if workspace_id:
        where += " AND [workspace_id] = ?"
        params.append(workspace_id)
    
    if not include_revoked:
        where += " AND [revoked_at] IS NULL"
    
    results = await db.find_entities(
        "api_keys",
        where_clause=where,
        params=tuple(params),
        order_by="[created_at] DESC",
    )
    
    keys = []
    for row in results:
        scopes = []
        if row.get("scopes"):
            try:
                scopes = json.loads(row["scopes"])
            except:
                pass
        
        keys.append({
            "id": row["id"],
            "name": row["name"],
            "key_prefix": row.get("key_prefix", "sk_..."),
            "scopes": scopes,
            "workspace_id": row.get("workspace_id"),
            "expires_at": row.get("expires_at"),
            "last_used_at": row.get("last_used_at"),
            "revoked_at": row.get("revoked_at"),
            "created_at": row.get("created_at"),
        })
    
    return keys


async def get_api_key(db, key_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """Get API key by ID (verifies ownership)."""
    import json
    
    results = await db.find_entities(
        "api_keys",
        where_clause="[id] = ? AND [user_id] = ?",
        params=(key_id, user_id),
        limit=1,
    )
    
    if not results:
        return None
    
    row = results[0]
    scopes = []
    if row.get("scopes"):
        try:
            scopes = json.loads(row["scopes"])
        except:
            pass
    
    return {
        "id": row["id"],
        "name": row["name"],
        "key_prefix": row.get("key_prefix", "sk_..."),
        "scopes": scopes,
        "workspace_id": row.get("workspace_id"),
        "expires_at": row.get("expires_at"),
        "last_used_at": row.get("last_used_at"),
        "revoked_at": row.get("revoked_at"),
        "created_at": row.get("created_at"),
    }


async def revoke_api_key(db, key_id: str, user_id: str) -> bool:
    """Revoke an API key (verifies ownership)."""
    # Verify ownership
    key = await get_api_key(db, key_id, user_id)
    if not key:
        return False
    
    await db.save_entity("api_keys", {
        "id": key_id,
        "revoked_at": _now_iso(),
        "updated_at": _now_iso(),
    })
    
    return True
