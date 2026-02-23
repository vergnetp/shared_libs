"""
Entity decorators for schema-first database design.

Allows defining entities as dataclasses with metadata for validation,
schema generation, and automatic migrations.

The @entity decorator auto-adds:
- System fields: id, created_at, updated_at, created_by, updated_by, deleted_at
  (injected automatically if not declared — apps only define business fields)
- from_dict(cls, data) - creates instance, deserializing JSON fields based on type hints
- get(cls, db, id) - fetch by ID (excludes soft-deleted by default)
- get_many(cls, db, ids) - batch fetch by IDs
- find(cls, db, where, params, ...) - query with filters (excludes soft-deleted by default)
- save(cls, db, data) - create/update (upsert) with auto id/timestamps
- update(cls, db, id, data) - merge update (fetch + merge + save)
- soft_delete(cls, db, id) - set deleted_at timestamp
- hard_delete(cls, db, id) - permanently remove row
- count(cls, db, where, params) - count matching entities

No separate store layer needed - the entity class IS the store.
"""

import json
import uuid
import functools
from dataclasses import field, Field, fields as dataclass_fields
from datetime import datetime, timezone
from typing import Any, Optional, Dict, List, get_type_hints, get_origin, get_args, TYPE_CHECKING


# Global registry of entity schemas
ENTITY_SCHEMAS: Dict[str, type] = {}

# System fields auto-injected by @entity if not already defined.
# These are kernel infrastructure — apps shouldn't have to declare them.
SYSTEM_FIELDS: Dict[str, Any] = {
    'id': Optional[str],
    'created_at': Optional[str],
    'updated_at': Optional[str],
    'created_by': Optional[str],
    'updated_by': Optional[str],
    'deleted_at': Optional[str],
}


# Sentinel for strict entity access - only entity methods should call db methods directly.
# When db._strict_entity_access is True, db methods like find_entities() and save_entity() 
# will raise unless this sentinel is passed as _caller. This prevents app code from
# bypassing entity classes (e.g. calling db.find_entities() instead of MyEntity.find()).
_ENTITY_CALLER = object()


# --- Auto-connection: lets entity methods work without an explicit db parameter ---
# Set via set_connection_provider() at app startup (typically by app_kernel bootstrap).
# When set, any entity classmethod (get, find, save, etc.) can be called with db=None
# and will auto-acquire a connection from the pool for just that single operation.
_connection_provider = None


def set_connection_provider(provider):
    """
    Register an async context manager that yields a db connection.
    
    Called once at startup by app_kernel. After this, entity methods
    can be called without a db parameter:
    
        project = await Project.get(id="abc")          # auto-acquires connection
        project = await Project.get(db, id="abc")      # explicit connection (batching)
    
    Args:
        provider: An async context manager factory, e.g. db_context from app_kernel.
    """
    global _connection_provider
    _connection_provider = provider


def _auto_db(fn):
    """
    Wrap an entity classmethod so db is optional.
    
    If db is None (or omitted), auto-acquires a connection from the pool
    for just this single call. If db is provided, uses it directly
    (allowing callers to batch multiple ops on one connection).
    """
    @functools.wraps(fn.__func__ if isinstance(fn, classmethod) else fn)
    async def wrapper(cls_or_self, db=None, *args, **kwargs):
        if db is not None:
            return await (fn.__func__ if isinstance(fn, classmethod) else fn)(cls_or_self, db, *args, **kwargs)
        if _connection_provider is None:
            raise RuntimeError(
                "No connection provider registered. Call set_connection_provider() at startup, "
                "or pass db explicitly."
            )
        async with _connection_provider() as auto_db:
            return await (fn.__func__ if isinstance(fn, classmethod) else fn)(cls_or_self, auto_db, *args, **kwargs)
    return classmethod(wrapper)


class EntityField:
    """Enhanced field metadata for entity attributes"""
    
    def __init__(
        self,
        default: Any = None,
        index: bool = False,
        unique: bool = False,
        nullable: bool = True,
        foreign_key: Optional[str] = None,
        check: Optional[str] = None,
    ):
        """
        Create an entity field with database metadata.
        
        Args:
            default: Default value for the field
            index: Whether to create an index on this field
            unique: Whether this field must be unique
            nullable: Whether NULL values are allowed
            foreign_key: Foreign key reference (e.g., "users.id")
            check: SQL CHECK constraint expression
        """
        self.default = default
        self.index = index
        self.unique = unique
        self.nullable = nullable
        self.foreign_key = foreign_key
        self.check = check


def entity_field(**kwargs) -> Field:
    """
    Create a dataclass field with entity metadata.
    
    Always provides a default (None unless specified), so field order never matters.
    
    Usage:
        @entity(table="users")
        @dataclass
        class User:
            email: str = entity_field(unique=True)      # default=None
            role: str = entity_field(default="user")    # default="user"
            tags: List[str] = entity_field(index=True)  # default=None
    """
    default_value = kwargs.pop('default', None)
    metadata = {k: v for k, v in kwargs.items()}
    return field(default=default_value, metadata=metadata)


def _inject_system_fields(cls):
    """
    Auto-inject system fields (id, created_at, ..., deleted_at) into a dataclass
    if not already defined. Re-runs @dataclass to regenerate __init__ etc.
    
    This means apps only declare their business fields:
    
        @entity(table="containers")
        @dataclass
        class Container:
            name: str = entity_field(nullable=False)
            # id, created_at, updated_at, created_by, updated_by, deleted_at
            # are all injected automatically
    """
    import dataclasses
    
    existing = {f.name for f in dataclass_fields(cls)}
    injected = False
    
    for name, type_hint in SYSTEM_FIELDS.items():
        if name not in existing:
            cls.__annotations__[name] = type_hint
            setattr(cls, name, None)
            injected = True
    
    if injected:
        # Re-run @dataclass to pick up the new fields in __init__, __repr__, etc.
        # Must delete existing dunders first — @dataclass skips if they exist
        for attr in ('__init__', '__repr__', '__eq__'):
            if attr in cls.__dict__:
                delattr(cls, attr)
        cls = dataclasses.dataclass(cls)
    
    return cls


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_id() -> str:
    return str(uuid.uuid4())


def _is_json_type(type_hint) -> bool:
    """Check if type hint indicates a JSON-serializable type (list, dict)."""
    if type_hint is None:
        return False
    origin = get_origin(type_hint)
    if origin in (list, dict, List, Dict):
        return True
    if type_hint in (list, dict):
        return True
    # Handle Optional[List[...]] etc
    if origin is type(None):
        return False
    args = get_args(type_hint)
    if args:
        return any(_is_json_type(arg) for arg in args)
    return False


def _add_dict_access(cls):
    """Add dict-like access (__getitem__, __setitem__, get, keys) to dataclass."""
    
    def __getitem__(self, key):
        return getattr(self, key)
    
    def __setitem__(self, key, value):
        setattr(self, key, value)
    
    def get_field(self, key, default=None):
        return getattr(self, key, default)
    
    def keys(self):
        return [f.name for f in dataclass_fields(self)]
    
    def items(self):
        return [(f.name, getattr(self, f.name)) for f in dataclass_fields(self)]
    
    def __iter__(self):
        return iter(self.keys())
    
    def __contains__(self, key):
        return hasattr(self, key)
    
    cls.__getitem__ = __getitem__
    cls.__setitem__ = __setitem__
    cls.get_field = get_field  # Renamed to avoid collision with CRUD .get()
    cls.keys = keys
    cls.items = items
    cls.__iter__ = __iter__
    cls.__contains__ = __contains__


def _get_base_type(type_hint):
    """
    Unwrap Optional/Union to get the base type.
    
    Optional[int] -> int, Optional[str] -> str, int -> int
    Union[int, None] -> int, Union[str, int] -> None (ambiguous)
    """
    origin = get_origin(type_hint)
    if origin is type(None):
        return None
    # Handle Optional[X] which is Union[X, None]
    args = get_args(type_hint)
    if args:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
        return None  # Ambiguous union
    return type_hint


def _make_from_dict(cls):
    """Create from_dict classmethod that deserializes fields based on type hints.
    
    Handles coercion for all types since database backends may return
    everything as strings (e.g. SQLite). Coercion maps are built once
    at decoration time for zero per-call overhead.
    """
    
    # Cache type hints at decoration time
    try:
        hints = get_type_hints(cls)
    except:
        hints = {}
    
    known_fields = {f.name for f in dataclass_fields(cls)}
    json_fields = {k for k, v in hints.items() if _is_json_type(v)}
    
    # Build coercion map: field_name -> base_type for primitive types
    # This handles int, float, bool coercion from string values
    _COERCE_TYPES = {int, float, bool}
    coerce_fields = {}  # field_name -> type
    for k, v in hints.items():
        base = _get_base_type(v)
        if base in _COERCE_TYPES:
            coerce_fields[k] = base
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        """
        Create entity from dict, filtering unknown fields and deserializing.
        
        Automatically coerces values to match type hints:
        - JSON fields (List, Dict) deserialized from strings
        - int/float fields cast from strings  
        - bool fields parsed from strings ('1'/'0'/'true'/'false')
        """
        if data is None:
            return None
        
        result = {}
        for k, v in data.items():
            if k not in known_fields:
                continue
            
            # Deserialize JSON string if field type is list/dict
            if k in json_fields and isinstance(v, str) and v:
                try:
                    v = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    pass
            
            # Coerce primitive types (int, float, bool) from strings
            # Databases like SQLite return everything as text
            elif k in coerce_fields and v is not None:
                target = coerce_fields[k]
                if not isinstance(v, target):
                    try:
                        if target is bool:
                            # Handle string booleans: '1'/'0'/'true'/'false'
                            if isinstance(v, str):
                                v = v.lower() in ('true', '1', 'yes', 'y', 't')
                            else:
                                v = bool(v)
                        else:
                            v = target(v)
                    except (ValueError, TypeError):
                        pass  # Keep original value if coercion fails
            
            result[k] = v
        
        return cls(**result)
    
    return from_dict


def _make_get(table_name: str, cls):
    """Create get classmethod."""
    @classmethod
    async def get(cls, db, id: str):
        """Fetch entity by ID."""
        results = await db.find_entities(
            table_name,
            where_clause="id = ?",
            params=(id,),
            limit=1,
            deserialize=False,
            _caller=_ENTITY_CALLER,
        )
        return cls.from_dict(results[0]) if results else None
    return get


def _make_get_many(table_name: str, cls):
    """Create get_many classmethod for batch fetching by IDs."""
    @classmethod
    async def get_many(cls, db, ids, include_deleted: bool = False):
        """
        Fetch multiple entities by IDs in a single query.
        
        Handles deduplication and chunking for large ID lists automatically.
        Returns entities in no guaranteed order — build a dict if you need
        keyed lookup: ``{e.id: e for e in await MyEntity.get_many(db, ids)}``
        
        Args:
            db: Database connection
            ids: Collection of entity IDs (list, set, or any iterable)
            include_deleted: Whether to include soft-deleted entities
            
        Returns:
            List of entities (may be fewer than input if some IDs not found)
        """
        id_list = list(ids) if not isinstance(ids, list) else ids
        if not id_list:
            return []
        results = await db.get_entities(
            table_name,
            entity_ids=id_list,
            include_deleted=include_deleted,
            deserialize=False,
            _caller=_ENTITY_CALLER,
        )
        return [cls.from_dict(r) for r in results]
    return get_many


def _make_find(table_name: str, cls):
    """Create find classmethod."""
    @classmethod
    async def find(cls, db, where: str = None, params: tuple = None,
                   order_by: str = None, limit: int = None, offset: int = None,
                   include_deleted: bool = False):
        """
        Query entities with filters.
        
        Args:
            db: Database connection
            where: WHERE clause (without 'WHERE')
            params: Parameters for WHERE clause
            order_by: ORDER BY clause
            limit: Max results
            offset: Skip N results
            include_deleted: Include soft-deleted entities
        """
        results = await db.find_entities(
            table_name,
            where_clause=where,
            params=params,
            order_by=order_by,
            limit=limit,
            offset=offset,
            include_deleted=include_deleted,
            deserialize=False,
            _caller=_ENTITY_CALLER,
        )
        return [cls.from_dict(r) for r in results]
    return find


def _make_save(table_name: str, cls):
    """Create save classmethod."""
    @classmethod
    async def save(cls, db, data: Dict[str, Any], match_by=None):
        """
        Create or update entity.
        
        Auto-generates id and timestamps if not provided.
        Returns the saved entity.
        
        Args:
            db: Database connection (optional — auto-acquires if None)
            data: Entity data dict
            match_by: Field name(s) to match existing entity by (for upsert without id).
                      E.g. match_by="name" or match_by=["workspace_id", "name"]
        """
        data = dict(data)  # Don't mutate original
        data['id'] = data.get('id') or _generate_id()
        data['created_at'] = data.get('created_at') or _now_iso()
        data['updated_at'] = _now_iso()
        
        await db.save_entity(table_name, data, match_by=match_by, _caller=_ENTITY_CALLER)
        return cls.from_dict(data)
    return save


def _make_soft_delete(table_name: str):
    """Create soft_delete classmethod — sets deleted_at timestamp."""
    @classmethod
    async def soft_delete(cls, db, id: str) -> bool:
        """Soft delete entity (set deleted_at). Record remains queryable with include_deleted=True."""
        await db.save_entity(table_name, {
            'id': id,
            'deleted_at': _now_iso(),
            'updated_at': _now_iso(),
        }, _caller=_ENTITY_CALLER)
        return True
    return soft_delete


def _make_hard_delete(table_name: str):
    """Create hard_delete classmethod — permanently removes row."""
    @classmethod
    async def hard_delete(cls, db, id: str) -> bool:
        """Permanently delete entity. Row is removed from database."""
        await db.delete_entity(table_name, id, permanent=True, _caller=_ENTITY_CALLER)
        return True
    return hard_delete


def _make_update(table_name: str, cls):
    """Create update classmethod that merges with existing."""
    @classmethod
    async def update(cls, db, id: str, data: Dict[str, Any]):
        """
        Update entity by ID (merge with existing).
        
        Fetches existing, merges with provided data, saves.
        Returns updated entity or None if not found.
        """
        existing = await cls.get(db, id)
        if not existing:
            return None
        
        # Merge: existing fields + new data
        from dataclasses import asdict
        merged = asdict(existing)
        merged.update(data)
        merged['updated_at'] = _now_iso()
        
        await db.save_entity(table_name, merged, _caller=_ENTITY_CALLER)
        return await cls.get(db, id)
    return update


def _make_count(table_name: str):
    """Create count classmethod."""
    @classmethod
    async def count(cls, db, where: str = None, params: tuple = None,
                    include_deleted: bool = False) -> int:
        """Count entities matching filter."""
        return await db.count_entities(
            table_name,
            where_clause=where,
            params=params,
            include_deleted=include_deleted,
            _caller=_ENTITY_CALLER,
        )
    return count


def _make_history(table_name: str, cls):
    """Create history classmethod — returns all versions of an entity."""
    @classmethod
    async def history(cls, db, id: str):
        """
        Get all historical versions of an entity.
        
        Returns list of entity instances, newest first.
        Requires history=True on @entity (default).
        """
        results = await db.get_entity_history(
            table_name, id, deserialize=False, _caller=_ENTITY_CALLER,
        )
        return [cls.from_dict(r) for r in results]
    return history


def _make_get_version(table_name: str, cls):
    """Create get_version classmethod — returns a specific version of an entity."""
    @classmethod
    async def get_version(cls, db, id: str, version: int):
        """
        Get a specific version of an entity.
        
        Args:
            db: Database connection (optional — auto-acquires if None)
            id: Entity ID
            version: Version number (1 = original, 2 = first update, etc.)
        """
        result = await db.get_entity_by_version(
            table_name, id, version, deserialize=False, _caller=_ENTITY_CALLER,
        )
        return cls.from_dict(result) if result else None
    return get_version


def entity(table: str = None, history: bool = True):
    """
    Decorator to mark a dataclass as a database entity.
    
    Auto-injects system fields (id, created_at, updated_at, created_by,
    updated_by, deleted_at) if not already declared, and adds CRUD methods.
    
    Usage:
        @entity(table="projects")
        @dataclass
        class Project:
            name: str
            tags: List[str] = entity_field(default=None)
            # id, created_at, updated_at, created_by, updated_by, deleted_at
            # are injected automatically — no need to declare them
        
        # The entity IS the store:
        project = await Project.get(db, "123")
        projects = await Project.find(db, where="name = ?", params=("test",))
        await Project.save(db, {"name": "new"})
        await Project.soft_delete(db, "123")
        await Project.hard_delete(db, "456")
    """
    def decorator(cls):
        tbl = table or cls.__name__.lower()
        
        # Auto-inject system fields (id, timestamps, deleted_at) if missing
        cls = _inject_system_fields(cls)
        
        # Add entity metadata
        cls.__entity_table__ = tbl
        cls.__entity_history__ = history
        
        # Add dict-like access (obj['key'] and obj.key both work)
        _add_dict_access(cls)
        
        # Add CRUD methods (always set - these are the primary API)
        # Note: cls.get from _add_dict_access is instance method for dict-like access
        # but we need classmethod for database operations, so we override it
        if not hasattr(cls, 'from_dict'):
            cls.from_dict = _make_from_dict(cls)
        
        # Always set CRUD classmethods (override dict-like .get if present)
        cls.get = _auto_db(_make_get(tbl, cls))
        cls.get_many = _auto_db(_make_get_many(tbl, cls))
        cls.find = _auto_db(_make_find(tbl, cls))
        cls.save = _auto_db(_make_save(tbl, cls))
        
        # Alias create -> save for backward compatibility
        if not hasattr(cls, 'create'):
            cls.create = cls.save
        
        cls.soft_delete = _auto_db(_make_soft_delete(tbl))
        cls.hard_delete = _auto_db(_make_hard_delete(tbl))
        cls.update = _auto_db(_make_update(tbl, cls))
        cls.count = _auto_db(_make_count(tbl))
        
        # History methods (only if history tracking enabled)
        if history:
            cls.history = _auto_db(_make_history(tbl, cls))
            cls.get_version = _auto_db(_make_get_version(tbl, cls))
        
        # Register in global schemas
        ENTITY_SCHEMAS[tbl] = cls
        
        return cls
    
    return decorator


def get_entity_schema(table_name: str) -> Optional[type]:
    """Get the entity class for a given table name."""
    return ENTITY_SCHEMAS.get(table_name)


def clear_entity_schemas():
    """Clear all registered entity schemas (useful for testing)."""
    ENTITY_SCHEMAS.clear()