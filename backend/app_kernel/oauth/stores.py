"""OAuth account storage."""

from datetime import datetime, timezone
from typing import Optional, Dict, Any, List


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_oauth_schema(db) -> None:
    """Create OAuth accounts table."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS oauth_accounts (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            provider_user_id TEXT NOT NULL,
            email TEXT,
            name TEXT,
            picture TEXT,
            access_token TEXT,
            refresh_token TEXT,
            token_expires_at TEXT,
            raw_data TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(provider, provider_user_id)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_oauth_user ON oauth_accounts(user_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_oauth_provider ON oauth_accounts(provider, provider_user_id)")


async def create_oauth_account(
    db,
    user_id: str,
    provider: str,
    provider_user_id: str,
    email: Optional[str] = None,
    name: Optional[str] = None,
    picture: Optional[str] = None,
    access_token: Optional[str] = None,
    refresh_token: Optional[str] = None,
    token_expires_at: Optional[str] = None,
    raw_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create or update an OAuth account link."""
    import uuid
    import json
    
    now = _now_iso()
    
    # Check if already exists
    existing = await get_oauth_account(db, provider, provider_user_id)
    
    if existing:
        # Update existing
        await db.save_entity("oauth_accounts", {
            "id": existing["id"],
            "user_id": user_id,
            "email": email,
            "name": name,
            "picture": picture,
            "access_token": access_token,
            "refresh_token": refresh_token or existing.get("refresh_token"),
            "token_expires_at": token_expires_at,
            "raw_data": json.dumps(raw_data) if raw_data else None,
            "updated_at": now,
        })
        return {**existing, "user_id": user_id, "updated_at": now}
    
    # Create new
    account_id = str(uuid.uuid4())
    await db.save_entity("oauth_accounts", {
        "id": account_id,
        "user_id": user_id,
        "provider": provider,
        "provider_user_id": provider_user_id,
        "email": email,
        "name": name,
        "picture": picture,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_expires_at": token_expires_at,
        "raw_data": json.dumps(raw_data) if raw_data else None,
        "created_at": now,
        "updated_at": now,
    })
    
    return {
        "id": account_id,
        "user_id": user_id,
        "provider": provider,
        "provider_user_id": provider_user_id,
        "email": email,
        "created_at": now,
    }


async def get_oauth_account(
    db,
    provider: str,
    provider_user_id: str,
) -> Optional[Dict[str, Any]]:
    """Get OAuth account by provider and provider user ID."""
    results = await db.find_entities(
        "oauth_accounts",
        where_clause="[provider] = ? AND [provider_user_id] = ?",
        params=(provider, provider_user_id),
        limit=1,
    )
    return results[0] if results else None


async def get_oauth_account_by_user(
    db,
    user_id: str,
    provider: str,
) -> Optional[Dict[str, Any]]:
    """Get OAuth account for a user and provider."""
    results = await db.find_entities(
        "oauth_accounts",
        where_clause="[user_id] = ? AND [provider] = ?",
        params=(user_id, provider),
        limit=1,
    )
    return results[0] if results else None


async def get_user_oauth_accounts(
    db,
    user_id: str,
) -> List[Dict[str, Any]]:
    """Get all OAuth accounts for a user."""
    results = await db.find_entities(
        "oauth_accounts",
        where_clause="[user_id] = ?",
        params=(user_id,),
    )
    
    # Don't expose tokens
    accounts = []
    for row in results:
        accounts.append({
            "id": row["id"],
            "provider": row["provider"],
            "provider_user_id": row["provider_user_id"],
            "email": row.get("email"),
            "name": row.get("name"),
            "picture": row.get("picture"),
            "created_at": row.get("created_at"),
        })
    
    return accounts


async def link_oauth_account(
    db,
    user_id: str,
    provider: str,
    provider_user_id: str,
    **kwargs,
) -> Dict[str, Any]:
    """Link an OAuth account to an existing user."""
    # Check if this OAuth account is already linked to another user
    existing = await get_oauth_account(db, provider, provider_user_id)
    if existing and existing["user_id"] != user_id:
        raise ValueError(f"This {provider} account is already linked to another user")
    
    return await create_oauth_account(
        db,
        user_id=user_id,
        provider=provider,
        provider_user_id=provider_user_id,
        **kwargs,
    )


async def unlink_oauth_account(
    db,
    user_id: str,
    provider: str,
) -> bool:
    """Unlink an OAuth account from a user."""
    account = await get_oauth_account_by_user(db, user_id, provider)
    if not account:
        return False
    
    await db.delete_entity("oauth_accounts", account["id"], permanent=True)
    return True


async def find_user_by_oauth(
    db,
    provider: str,
    provider_user_id: str,
) -> Optional[str]:
    """Find user ID by OAuth account. Returns user_id or None."""
    account = await get_oauth_account(db, provider, provider_user_id)
    return account["user_id"] if account else None
