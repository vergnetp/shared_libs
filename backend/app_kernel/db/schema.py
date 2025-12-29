"""
Kernel infrastructure schema.

Creates tables owned by app_kernel:
- jobs: Background job tracking
- audit_log: Request audit trail
- rate_limits: Rate limiting state
- idempotency_keys: Request deduplication
- users: Authentication users

Apps should NOT create these tables - kernel owns them.
"""

from typing import Any


async def init_kernel_schema(conn: Any) -> None:
    """
    Create kernel-owned infrastructure tables.
    
    Call this from kernel initialization, before app schema.
    Safe to call multiple times (CREATE IF NOT EXISTS).
    
    Args:
        conn: Database connection with execute() method
    """
    # Jobs table - background job tracking
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            task TEXT NOT NULL,
            payload TEXT,
            context TEXT,
            status TEXT DEFAULT 'queued',
            result TEXT,
            error TEXT,
            attempts INTEGER DEFAULT 0,
            max_attempts INTEGER DEFAULT 3,
            priority TEXT DEFAULT 'normal',
            user_id TEXT,
            idempotency_key TEXT,
            created_at TEXT,
            started_at TEXT,
            completed_at TEXT
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_idempotency ON jobs(idempotency_key)")
    
    # Audit log - request audit trail
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            request_id TEXT,
            user_id TEXT,
            action TEXT NOT NULL,
            resource_type TEXT,
            resource_id TEXT,
            details TEXT DEFAULT '{}',
            ip_address TEXT,
            user_agent TEXT
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_request ON audit_log(request_id)")
    
    # Rate limits - sliding window rate limiting
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS rate_limits (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            ip_address TEXT,
            endpoint TEXT,
            window_start TEXT,
            request_count INTEGER DEFAULT 0,
            UNIQUE(user_id, ip_address, endpoint, window_start)
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_rate_limits_lookup ON rate_limits(user_id, ip_address, endpoint)")
    
    # Idempotency keys - request deduplication
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            key TEXT PRIMARY KEY,
            user_id TEXT,
            endpoint TEXT,
            request_hash TEXT,
            response TEXT,
            status_code INTEGER,
            created_at TEXT,
            expires_at TEXT
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_idempotency_expires ON idempotency_keys(expires_at)")
    
    # Users - authentication
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT,
            name TEXT,
            role TEXT DEFAULT 'user',
            is_active INTEGER DEFAULT 1,
            metadata TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")


async def cleanup_expired_idempotency_keys(conn: Any) -> int:
    """
    Remove expired idempotency keys.
    
    Call periodically (e.g., hourly) to prevent table bloat.
    
    Returns:
        Number of keys removed
    """
    from datetime import datetime, timezone
    
    now = datetime.now(timezone.utc).isoformat()
    
    # Count before delete
    result = await conn.execute(
        "SELECT COUNT(*) FROM idempotency_keys WHERE expires_at < ?",
        (now,)
    )
    count = result[0][0] if result else 0
    
    # Delete expired
    await conn.execute(
        "DELETE FROM idempotency_keys WHERE expires_at < ?",
        (now,)
    )
    
    return count


async def cleanup_old_rate_limits(conn: Any, older_than_hours: int = 24) -> int:
    """
    Remove old rate limit entries.
    
    Call periodically to prevent table bloat.
    
    Args:
        older_than_hours: Remove entries older than this
        
    Returns:
        Number of entries removed
    """
    from datetime import datetime, timezone, timedelta
    
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).isoformat()
    
    result = await conn.execute(
        "SELECT COUNT(*) FROM rate_limits WHERE window_start < ?",
        (cutoff,)
    )
    count = result[0][0] if result else 0
    
    await conn.execute(
        "DELETE FROM rate_limits WHERE window_start < ?",
        (cutoff,)
    )
    
    return count
