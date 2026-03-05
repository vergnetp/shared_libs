"""
DEPRECATED: SaaS schema is now defined via @entity decorators in app_kernel/schema.py.
Tables are created automatically by AutoMigrator at startup.

These shims exist only for backwards compatibility with code that imports them.
"""
import warnings

# Legacy — kept for import compat only
SAAS_TABLES = {}


def get_saas_table_sql(table_name: str) -> str:
    """DEPRECATED: Schema is auto-managed by @entity + AutoMigrator."""
    warnings.warn(
        "get_saas_table_sql() is deprecated — schema is auto-managed via @entity decorators",
        DeprecationWarning,
        stacklevel=2,
    )
    return ""


def get_all_saas_tables_sql() -> list:
    """DEPRECATED: Schema is auto-managed by @entity + AutoMigrator."""
    warnings.warn(
        "get_all_saas_tables_sql() is deprecated — schema is auto-managed via @entity decorators",
        DeprecationWarning,
        stacklevel=2,
    )
    return []
