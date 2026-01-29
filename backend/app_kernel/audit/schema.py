"""Audit schema - creates table in admin_db."""


async def init_audit_schema(admin_db) -> None:
    """Create audit_logs table in admin_db."""
    await admin_db.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id TEXT PRIMARY KEY,
            app TEXT NOT NULL,
            entity TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            action TEXT NOT NULL,
            changes TEXT,
            old_snapshot TEXT,
            new_snapshot TEXT,
            user_id TEXT,
            request_id TEXT,
            timestamp TEXT NOT NULL,
            created_at TEXT
        )
    """)
    await admin_db.execute("CREATE INDEX IF NOT EXISTS idx_audit_app ON audit_logs(app, timestamp)")
    await admin_db.execute("CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_logs(app, entity, entity_id)")
    await admin_db.execute("CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(app, user_id, timestamp)")
