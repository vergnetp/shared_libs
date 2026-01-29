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
    
    # Raw usage events (for detailed tracking and later aggregation)
    await admin_db.execute("""
        CREATE TABLE IF NOT EXISTS usage_events (
            id TEXT PRIMARY KEY,
            app TEXT NOT NULL,
            workspace_id TEXT,
            user_id TEXT,
            event_type TEXT DEFAULT 'request',
            endpoint TEXT,
            method TEXT,
            status_code INTEGER,
            latency_ms INTEGER,
            bytes_in INTEGER,
            bytes_out INTEGER,
            period TEXT,
            timestamp TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await admin_db.execute("CREATE INDEX IF NOT EXISTS idx_events_app ON usage_events(app, timestamp)")
    await admin_db.execute("CREATE INDEX IF NOT EXISTS idx_events_workspace ON usage_events(app, workspace_id, timestamp)")
    await admin_db.execute("CREATE INDEX IF NOT EXISTS idx_events_period ON usage_events(app, period)")
