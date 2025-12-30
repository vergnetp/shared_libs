"""
Database schema for deploy API.

Tables:
- workspaces: Tenant/organization
- workspace_members: User membership in workspaces
- projects: Deployment projects
- credentials: Encrypted credentials per project/env
- deployment_runs: Deployment history (supplements kernel jobs table)
"""

SCHEMA_SQL = """
-- Workspaces (tenants)
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    owner_id TEXT NOT NULL,
    plan TEXT DEFAULT 'free',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workspaces_owner ON workspaces(owner_id);
CREATE INDEX IF NOT EXISTS idx_workspaces_name ON workspaces(name);

-- Workspace members
CREATE TABLE IF NOT EXISTS workspace_members (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    joined_at TEXT NOT NULL,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
    UNIQUE(workspace_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_workspace_members_workspace ON workspace_members(workspace_id);
CREATE INDEX IF NOT EXISTS idx_workspace_members_user ON workspace_members(user_id);

-- Projects
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    docker_hub_user TEXT NOT NULL,
    version TEXT DEFAULT 'latest',
    config_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
    UNIQUE(workspace_id, name)
);

CREATE INDEX IF NOT EXISTS idx_projects_workspace ON projects(workspace_id);
CREATE INDEX IF NOT EXISTS idx_projects_name ON projects(name);

-- Credentials (encrypted)
CREATE TABLE IF NOT EXISTS credentials (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    project_name TEXT NOT NULL,
    env TEXT NOT NULL,
    encrypted_blob TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
    UNIQUE(workspace_id, project_name, env)
);

CREATE INDEX IF NOT EXISTS idx_credentials_lookup ON credentials(workspace_id, project_name, env);

-- Deployment runs (history, supplements kernel jobs)
CREATE TABLE IF NOT EXISTS deployment_runs (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    project_name TEXT NOT NULL,
    env TEXT NOT NULL,
    services TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    triggered_by TEXT NOT NULL,
    triggered_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    result_json TEXT,
    error TEXT,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_deployment_runs_workspace ON deployment_runs(workspace_id);
CREATE INDEX IF NOT EXISTS idx_deployment_runs_project ON deployment_runs(workspace_id, project_name);
CREATE INDEX IF NOT EXISTS idx_deployment_runs_job ON deployment_runs(job_id);
CREATE INDEX IF NOT EXISTS idx_deployment_runs_status ON deployment_runs(status);

-- Deployment state (replaces deployments.json)
CREATE TABLE IF NOT EXISTS deployment_state (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    project_name TEXT NOT NULL,
    env TEXT NOT NULL,
    state_json TEXT NOT NULL DEFAULT '{}',
    last_deployed_at TEXT,
    last_deployed_by TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
    UNIQUE(workspace_id, project_name, env)
);

CREATE INDEX IF NOT EXISTS idx_deployment_state_lookup ON deployment_state(workspace_id, project_name, env);
"""


async def init_deploy_schema(db):
    """Initialize deploy API schema."""
    statements = [s.strip() for s in SCHEMA_SQL.split(';') if s.strip()]
    for statement in statements:
        await db.execute(statement, ())
