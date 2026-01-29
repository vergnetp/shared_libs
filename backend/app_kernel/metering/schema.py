"""Metering schema - creates tables in admin_db."""


async def init_metering_schema(admin_db) -> None:
    """Create metering tables in admin_db."""
    
    # Aggregated usage summary (main table for billing)
    await admin_db.execute("""
        CREATE TABLE IF NOT EXISTS usage_summary (
            id TEXT PRIMARY KEY,
            app TEXT NOT NULL,
            workspace_id TEXT,
            user_id TEXT,
            period TEXT NOT NULL,
            metric TEXT NOT NULL,
            value INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(app, workspace_id, user_id, period, metric)
        )
    """)
    await admin_db.execute("CREATE INDEX IF NOT EXISTS idx_usage_app ON usage_summary(app, period)")
    await admin_db.execute("CREATE INDEX IF NOT EXISTS idx_usage_workspace ON usage_summary(app, workspace_id, period)")
    await admin_db.execute("CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_summary(app, user_id, period)")
    
    # Optional: detailed request log (for debugging, not billing)
    await admin_db.execute("""
        CREATE TABLE IF NOT EXISTS usage_requests (
            id TEXT PRIMARY KEY,
            app TEXT NOT NULL,
            user_id TEXT,
            workspace_id TEXT,
            endpoint TEXT,
            method TEXT,
            status_code INTEGER,
            latency_ms INTEGER,
            bytes_in INTEGER,
            bytes_out INTEGER,
            timestamp TEXT,
            created_at TEXT
        )
    """)
    await admin_db.execute("CREATE INDEX IF NOT EXISTS idx_requests_app ON usage_requests(app, timestamp)")
    await admin_db.execute("CREATE INDEX IF NOT EXISTS idx_requests_workspace ON usage_requests(app, workspace_id, timestamp)")
