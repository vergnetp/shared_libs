"""
Database schema - AUTO-GENERATED from manifest.yaml
DO NOT EDIT - changes will be overwritten on regenerate
"""

from typing import Any


async def init_schema(db: Any) -> None:
    """Initialize database schema. Called by kernel after DB connection."""

    # Workspace
    await db.execute("""
        CREATE TABLE IF NOT EXISTS workspaces (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            metadata TEXT,
            created_at TEXT,
            updated_at TEXT,
            deleted_at TEXT
        )
    """)

    # WorkspaceMember
    await db.execute("""
        CREATE TABLE IF NOT EXISTS workspace_members (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            user_id TEXT NOT NULL,
            role TEXT DEFAULT 'member',
            created_at TEXT,
            updated_at TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_workspace_members_workspace ON workspace_members(workspace_id)")

    # Agent
    await db.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            name TEXT NOT NULL,
            system_prompt TEXT,
            model TEXT DEFAULT 'llama-3.3-70b-versatile',
            premium_model TEXT,
            temperature REAL DEFAULT 0.5,
            max_tokens INTEGER DEFAULT 4096,
            tools TEXT,
            guardrails TEXT,
            memory_strategy TEXT DEFAULT 'last_n',
            memory_params TEXT,
            context_schema TEXT,
            capabilities TEXT,
            owner_user_id TEXT,
            metadata TEXT,
            created_by TEXT,
            updated_by TEXT,
            created_at TEXT,
            updated_at TEXT,
            deleted_at TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_agents_workspace ON agents(workspace_id)")

    # Thread
    await db.execute("""
        CREATE TABLE IF NOT EXISTS threads (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            agent_id TEXT NOT NULL,
            title TEXT,
            summary TEXT,
            turn_count INTEGER,
            token_count INTEGER,
            owner_user_id TEXT,
            metadata TEXT,
            created_at TEXT,
            updated_at TEXT,
            deleted_at TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_threads_workspace ON threads(workspace_id)")

    # Message
    await db.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            tool_calls TEXT,
            tool_results TEXT,
            model TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cost REAL,
            latency_ms INTEGER,
            metadata TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    # Document
    await db.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            agent_id TEXT,
            filename TEXT NOT NULL,
            content_type TEXT,
            size INTEGER,
            chunk_count INTEGER,
            status TEXT DEFAULT 'pending',
            error TEXT,
            metadata TEXT,
            processed_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            deleted_at TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_documents_workspace ON documents(workspace_id)")

    # DocumentChunk
    await db.execute("""
        CREATE TABLE IF NOT EXISTS document_chunks (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            embedding TEXT,
            metadata TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    # UserContext
    await db.execute("""
        CREATE TABLE IF NOT EXISTS user_contexts (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            user_id TEXT NOT NULL,
            context_type TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT,
            expires_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_user_contexts_workspace ON user_contexts(workspace_id)")

    # AnalyticsDaily
    await db.execute("""
        CREATE TABLE IF NOT EXISTS analytics_dailies (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            date TEXT NOT NULL,
            agent_id TEXT,
            user_id TEXT,
            message_count INTEGER,
            thread_count INTEGER,
            token_count INTEGER,
            cost REAL,
            avg_latency_ms INTEGER,
            error_count INTEGER,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_analytics_dailies_workspace ON analytics_dailies(workspace_id)")
