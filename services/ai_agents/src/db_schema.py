"""
Database schema for agent service.

This is passed to create_service(schema_init=...) and called by the kernel
AFTER the database connection is initialized.

Tables created:
- workspaces: Multi-tenant isolation
- workspace_members: User membership
- agents: AI agent configurations
- threads: Conversation threads
- messages: Thread messages
- documents: RAG documents
- user_context: User context storage

Note: Infrastructure tables (jobs, audit_log, rate_limits, idempotency_keys, users)
are created by app_kernel. This module only creates domain-specific tables.
"""

from typing import Any


async def init_agent_schema(db: Any) -> None:
    """
    Initialize agent service schema.
    
    Called by kernel's bootstrap after database connection is established.
    Safe to call multiple times (uses CREATE IF NOT EXISTS).
    
    Args:
        db: Database connection with execute() method
    """
    
    # Helper for migrations
    async def add_column_if_missing(table: str, column: str, col_type: str, default: str = None):
        """Add column to existing table if it doesn't exist (SQLite)."""
        try:
            result = await db.execute(f"PRAGMA table_info({table})")
            columns = [row[1] for row in result] if result else []
            
            if column not in columns:
                default_clause = f" DEFAULT {default}" if default else ""
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}{default_clause}")
        except Exception:
            pass
    
    # =========================================================================
    # Workspaces
    # =========================================================================
    await db.execute("""
        CREATE TABLE IF NOT EXISTS workspaces (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            metadata TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT,
            deleted_at TEXT
        )
    """)
    
    # =========================================================================
    # Workspace Members
    # =========================================================================
    await db.execute("""
        CREATE TABLE IF NOT EXISTS workspace_members (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(workspace_id, user_id)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_workspace_members_user ON workspace_members(user_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_workspace_members_workspace ON workspace_members(workspace_id)")
    
    # =========================================================================
    # Agents
    # =========================================================================
    await db.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            system_prompt TEXT,
            model TEXT DEFAULT 'claude-sonnet-4-20250514',
            provider TEXT DEFAULT 'anthropic',
            premium_provider TEXT,
            premium_model TEXT,
            temperature REAL DEFAULT 0.7,
            max_tokens INTEGER DEFAULT 4096,
            tools TEXT DEFAULT '[]',
            guardrails TEXT DEFAULT '[]',
            memory_strategy TEXT DEFAULT 'last_n',
            memory_params TEXT DEFAULT '{"n": 20}',
            context_schema TEXT,
            capabilities TEXT DEFAULT '[]',
            owner_user_id TEXT,
            workspace_id TEXT,
            metadata TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT,
            created_by TEXT,
            updated_by TEXT,
            deleted_at TEXT
        )
    """)
    # Migrations for existing tables
    await add_column_if_missing("agents", "owner_user_id", "TEXT")
    await add_column_if_missing("agents", "workspace_id", "TEXT")
    await add_column_if_missing("agents", "capabilities", "TEXT", "'[]'")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_agents_owner ON agents(owner_user_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_agents_workspace ON agents(workspace_id)")
    
    # =========================================================================
    # Threads
    # =========================================================================
    await db.execute("""
        CREATE TABLE IF NOT EXISTS threads (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            title TEXT,
            summary TEXT,
            turn_count INTEGER DEFAULT 0,
            token_count INTEGER DEFAULT 0,
            owner_user_id TEXT,
            workspace_id TEXT,
            metadata TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT,
            deleted_at TEXT
        )
    """)
    await add_column_if_missing("threads", "owner_user_id", "TEXT")
    await add_column_if_missing("threads", "workspace_id", "TEXT")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_threads_agent ON threads(agent_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_threads_owner ON threads(owner_user_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_threads_workspace ON threads(workspace_id)")
    
    # =========================================================================
    # Messages
    # =========================================================================
    await db.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            tool_calls TEXT,
            tool_results TEXT,
            model TEXT,
            provider TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cost REAL DEFAULT 0,
            latency_ms INTEGER DEFAULT 0,
            metadata TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at)")
    
    # =========================================================================
    # Documents (RAG)
    # =========================================================================
    await db.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            agent_id TEXT,
            workspace_id TEXT,
            filename TEXT NOT NULL,
            content_type TEXT,
            size INTEGER,
            chunk_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            error TEXT,
            metadata TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT,
            processed_at TEXT,
            deleted_at TEXT
        )
    """)
    await add_column_if_missing("documents", "workspace_id", "TEXT")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_documents_agent ON documents(agent_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_documents_workspace ON documents(workspace_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status)")
    
    # =========================================================================
    # Document Chunks (for vector search)
    # =========================================================================
    await db.execute("""
        CREATE TABLE IF NOT EXISTS document_chunks (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            embedding TEXT,
            metadata TEXT DEFAULT '{}',
            created_at TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_chunks_document ON document_chunks(document_id)")
    
    # =========================================================================
    # User Context
    # =========================================================================
    await db.execute("""
        CREATE TABLE IF NOT EXISTS user_context (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            workspace_id TEXT,
            context_type TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT,
            expires_at TEXT
        )
    """)
    await add_column_if_missing("user_context", "workspace_id", "TEXT")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_user_context_user ON user_context(user_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_user_context_workspace ON user_context(workspace_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_user_context_type ON user_context(context_type)")
    
    # =========================================================================
    # Analytics (aggregated stats)
    # =========================================================================
    await db.execute("""
        CREATE TABLE IF NOT EXISTS analytics_daily (
            id TEXT PRIMARY KEY,
            date TEXT NOT NULL,
            workspace_id TEXT,
            agent_id TEXT,
            user_id TEXT,
            message_count INTEGER DEFAULT 0,
            thread_count INTEGER DEFAULT 0,
            token_count INTEGER DEFAULT 0,
            cost REAL DEFAULT 0,
            avg_latency_ms INTEGER DEFAULT 0,
            error_count INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(date, workspace_id, agent_id, user_id)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_analytics_date ON analytics_daily(date)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_analytics_workspace ON analytics_daily(workspace_id)")
