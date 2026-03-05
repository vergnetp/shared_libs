"""
DEPRECATED: Auth schema is now defined via @entity decorators in app_kernel/schema.py.
Tables are created automatically by AutoMigrator at startup.

These shims exist only for backwards compatibility with code that imports them.
"""
import warnings


async def init_auth_schema(db) -> None:
    """DEPRECATED: Schema is auto-managed by @entity + AutoMigrator."""
    warnings.warn(
        "init_auth_schema() is deprecated — schema is auto-managed via @entity decorators",
        DeprecationWarning,
        stacklevel=2,
    )


async def migrate_add_identity_hash(db) -> None:
    """DEPRECATED: Migrations are auto-managed by AutoMigrator."""
    warnings.warn(
        "migrate_add_identity_hash() is deprecated — use AutoMigrator",
        DeprecationWarning,
        stacklevel=2,
    )
