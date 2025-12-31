"""
Database schema - AUTO-GENERATED from manifest.yaml
DO NOT EDIT - changes will be overwritten on regenerate
"""

from typing import Any


async def init_schema(db: Any) -> None:
    """Initialize database schema. Called by kernel after DB connection."""

    # NOTE: Auth tables (auth_users, auth_roles, etc.) are created
    # automatically by the kernel when auth_enabled=True

    # Workspace
    await db.execute("""
        CREATE TABLE IF NOT EXISTS workspaces (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            plan TEXT DEFAULT 'free',
            created_at TEXT,
            updated_at TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_workspaces_name ON workspaces(name)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_workspaces_owner ON workspaces(owner_id)")

    # WorkspaceMember
    await db.execute("""
        CREATE TABLE IF NOT EXISTS workspace_members (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            user_id TEXT NOT NULL,
            role TEXT DEFAULT 'member',
            joined_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
            UNIQUE(workspace_id, user_id)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_workspace_members_workspace ON workspace_members(workspace_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_workspace_members_user ON workspace_members(user_id)")

    # Project
    await db.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            name TEXT NOT NULL,
            docker_hub_user TEXT NOT NULL,
            version TEXT DEFAULT 'latest',
            config_json TEXT,
            created_by TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
            UNIQUE(workspace_id, name)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_projects_workspace ON projects(workspace_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_projects_name ON projects(name)")

    # Credential
    await db.execute("""
        CREATE TABLE IF NOT EXISTS credentials (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            project_name TEXT NOT NULL,
            env TEXT NOT NULL,
            encrypted_blob TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
            UNIQUE(workspace_id, project_name, env)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_credentials_workspace ON credentials(workspace_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_credentials_lookup ON credentials(workspace_id, project_name, env)")

    # DeploymentRun
    await db.execute("""
        CREATE TABLE IF NOT EXISTS deployment_runs (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            job_id TEXT NOT NULL,
            project_name TEXT NOT NULL,
            env TEXT NOT NULL,
            services TEXT,
            status TEXT DEFAULT 'queued',
            triggered_by TEXT NOT NULL,
            triggered_at TEXT,
            started_at TEXT,
            completed_at TEXT,
            result_json TEXT,
            error TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_deployment_runs_workspace ON deployment_runs(workspace_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_deployment_runs_job ON deployment_runs(job_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_deployment_runs_project ON deployment_runs(workspace_id, project_name)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_deployment_runs_status ON deployment_runs(status)")

    # DeploymentState
    await db.execute("""
        CREATE TABLE IF NOT EXISTS deployment_state (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            project_name TEXT NOT NULL,
            env TEXT NOT NULL,
            state_json TEXT,
            last_deployed_at TEXT,
            last_deployed_by TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
            UNIQUE(workspace_id, project_name, env)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_deployment_state_workspace ON deployment_state(workspace_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_deployment_state_lookup ON deployment_state(workspace_id, project_name, env)")
