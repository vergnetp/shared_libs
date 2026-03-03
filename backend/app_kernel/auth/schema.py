"""
Auth database schema - creates auth tables automatically.

Tables created:
- auth_users: User accounts (email nullable, identity_hash for external identity)
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
    # email: nullable — DO-authenticated users may not have one yet.
    # identity_hash: nullable unique — SHA256(DO UUID) for external identity lookup.
    #   Regular email/password users don't have one.
    await db.execute("""
        CREATE TABLE IF NOT EXISTS auth_users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE,
            identity_hash TEXT UNIQUE,
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
    await db.execute("CREATE INDEX IF NOT EXISTS idx_auth_users_identity_hash ON auth_users(identity_hash)")
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


# Migration helper — call once on existing DBs that already have auth_users.
async def migrate_add_identity_hash(db) -> None:
    """Add identity_hash column to existing auth_users table.
    
    Safe to call multiple times — silently skips if column exists.
    """
    try:
        await db.execute("ALTER TABLE auth_users ADD COLUMN identity_hash TEXT UNIQUE")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_auth_users_identity_hash ON auth_users(identity_hash)")
    except Exception:
        pass  # Column already exists

    # Make email nullable (SQLite doesn't support ALTER COLUMN, but it never
    # actually enforces NOT NULL on existing rows — new inserts with NULL email
    # will work if the column was originally NOT NULL in SQLite).
    # For PostgreSQL, uncomment:
    # await db.execute("ALTER TABLE auth_users ALTER COLUMN email DROP NOT NULL")


# PostgreSQL version (for reference - adjust types)
POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE,
    identity_hash VARCHAR(64) UNIQUE,
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
