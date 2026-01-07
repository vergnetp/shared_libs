"""
SaaS module database schema.

Tables for multi-tenant SaaS applications:
- workspaces: Teams/organizations
- workspace_members: User membership in workspaces
- workspace_invites: Pending invitations
- projects: Deployment groupings within workspaces
"""

SAAS_TABLES = {
    "workspaces": {
        "id": "TEXT PRIMARY KEY",
        "name": "TEXT NOT NULL",
        "slug": "TEXT UNIQUE",  # URL-friendly name
        "owner_id": "TEXT NOT NULL",  # User who created it
        "is_personal": "INTEGER DEFAULT 0",  # Auto-created personal workspace
        "settings_json": "TEXT",  # Workspace-level settings
        "created_at": "TEXT",
        "updated_at": "TEXT",
    },
    "workspace_members": {
        "id": "TEXT PRIMARY KEY",
        "workspace_id": "TEXT NOT NULL",
        "user_id": "TEXT NOT NULL",
        "role": "TEXT DEFAULT 'member'",  # owner, admin, member
        "invited_by": "TEXT",
        "joined_at": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
        # Composite unique constraint handled by store
    },
    "workspace_invites": {
        "id": "TEXT PRIMARY KEY",
        "workspace_id": "TEXT NOT NULL",
        "email": "TEXT NOT NULL",
        "role": "TEXT DEFAULT 'member'",
        "token": "TEXT UNIQUE NOT NULL",  # For accept link
        "invited_by": "TEXT NOT NULL",
        "status": "TEXT DEFAULT 'pending'",  # pending, accepted, expired, cancelled
        "expires_at": "TEXT",
        "accepted_at": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    },
    "projects": {
        "id": "TEXT PRIMARY KEY",
        "workspace_id": "TEXT NOT NULL",
        "name": "TEXT NOT NULL",  # e.g., "hostomatic", "ai-mediator"
        "slug": "TEXT NOT NULL",  # URL-friendly, unique within workspace
        "description": "TEXT",
        "settings_json": "TEXT",  # Project-level defaults (region, size, etc.)
        "created_by": "TEXT NOT NULL",
        "created_at": "TEXT",
        "updated_at": "TEXT",
        # Note: (workspace_id, slug) should be unique - enforced by store
    },
}


def get_saas_table_sql(table_name: str) -> str:
    """Generate CREATE TABLE SQL for a SaaS table."""
    if table_name not in SAAS_TABLES:
        raise ValueError(f"Unknown SaaS table: {table_name}")
    
    columns = SAAS_TABLES[table_name]
    col_defs = [f"{col} {typedef}" for col, typedef in columns.items()]
    
    return f"CREATE TABLE IF NOT EXISTS {table_name} ({', '.join(col_defs)})"


def get_all_saas_tables_sql() -> list:
    """Get CREATE TABLE SQL for all SaaS tables."""
    return [get_saas_table_sql(name) for name in SAAS_TABLES.keys()]
