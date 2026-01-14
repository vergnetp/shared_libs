"""
Auth database schema - creates auth tables automatically.

Tables created:
- auth_users: User accounts
- auth_roles: Role definitions with permissions
- auth_role_assignments: User-role mappings (optionally scoped to resources)
"""


async def init_auth_schema(db) -> None:
    """
    Initialize auth tables in the database.
    
    Called automatically by kernel when:
    - auth_enabled=True (default)
    - database is configured
    
    Args:
        db: Database connection from kernel's pool
    """
    
    # Users table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS auth_users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT,
            name TEXT,
            role TEXT DEFAULT 'user',
            metadata TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT,
            deleted_at TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_auth_users_email ON auth_users(email)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_auth_users_role ON auth_users(role)")
    
    # Roles table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS auth_roles (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            permissions TEXT,
            description TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_auth_roles_name ON auth_roles(name)")
    
    # Role assignments table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS auth_role_assignments (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            role_id TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            resource_id TEXT,
            granted_by TEXT,
            created_at TEXT,
            expires_at TEXT,
            FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
            FOREIGN KEY (role_id) REFERENCES auth_roles(id) ON DELETE CASCADE
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_auth_role_assignments_user 
        ON auth_role_assignments(user_id)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_auth_role_assignments_resource 
        ON auth_role_assignments(resource_type, resource_id)
    """)
    
    # Sessions table (for token revocation)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS auth_sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            token_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            metadata TEXT,
            created_at TEXT,
            FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_token ON auth_sessions(token_hash)")


# PostgreSQL version (for reference - adjust types)
POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255),
    name VARCHAR(255),
    role VARCHAR(50) DEFAULT 'user',
    metadata JSONB DEFAULT '{}',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    deleted_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS auth_roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL UNIQUE,
    permissions JSONB DEFAULT '[]',
    description TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS auth_role_assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    role_id UUID NOT NULL REFERENCES auth_roles(id) ON DELETE CASCADE,
    resource_type VARCHAR(100) NOT NULL,
    resource_id VARCHAR(255),
    granted_by UUID,
    created_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP
);
"""
