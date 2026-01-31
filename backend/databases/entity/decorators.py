"""
Entity decorators for schema-first database design.

Allows defining entities as dataclasses with metadata for validation,
schema generation, and automatic migrations.

The @entity decorator auto-adds:
- from_dict(cls, data) - creates instance, deserializing JSON fields based on type hints
- get(cls, db, id) - fetch by ID
- find(cls, db, where, params, ...) - query with filters
- save(cls, db, data) - create/update with auto id/timestamps
- delete(cls, db, id) - hard delete
- soft_delete(cls, db, id) - set deleted_at

No separate store layer needed - the entity class IS the store.
"""

import json
import uuid
from dataclasses import field, Field, fields as dataclass_fields
from datetime import datetime, timezone
from typing import Any, Optional, Dict, List, get_type_hints, get_origin, get_args, TYPE_CHECKING


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


# Global registry of entity schemas
ENTITY_SCHEMAS: Dict[str, type] = {}


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
    
    def get(self, key, default=None):
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
    cls.get = get
    cls.keys = keys
    cls.items = items
    cls.__iter__ = __iter__
    cls.__contains__ = __contains__


def _make_from_dict(cls):
    """Create from_dict classmethod that deserializes JSON fields based on type hints."""
    
    # Cache type hints at decoration time
    try:
        hints = get_type_hints(cls)
    except:
        hints = {}
    
    known_fields = {f.name for f in dataclass_fields(cls)}
    json_fields = {k for k, v in hints.items() if _is_json_type(v)}
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        """
        Create entity from dict, filtering unknown fields and deserializing JSON.
        
        JSON fields (List, Dict types) are automatically deserialized from strings.
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
        )
        return cls.from_dict(results[0]) if results else None
    return get


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
        )
        return [cls.from_dict(r) for r in results]
    return find


def _make_save(table_name: str, cls):
    """Create save classmethod."""
    @classmethod
    async def save(cls, db, data: Dict[str, Any]):
        """
        Create or update entity.
        
        Auto-generates id and timestamps if not provided.
        Returns the saved entity.
        """
        data = dict(data)  # Don't mutate original
        data['id'] = data.get('id') or _generate_id()
        data['created_at'] = data.get('created_at') or _now_iso()
        data['updated_at'] = _now_iso()
        
        await db.save_entity(table_name, data)
        return cls.from_dict(data)
    return save


def _make_delete(table_name: str):
    """Create delete classmethod."""
    @classmethod
    async def delete(cls, db, id: str, permanent: bool = False):
        """
        Delete entity by ID.
        
        Args:
            permanent: If True, hard delete. If False, soft delete (set deleted_at).
        """
        if permanent:
            await db.execute(f"DELETE FROM {table_name} WHERE id = ?", (id,))
        else:
            await db.save_entity(table_name, {
                'id': id,
                'deleted_at': _now_iso(),
                'updated_at': _now_iso(),
            })
        return True
    return delete


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
        
        await db.save_entity(table_name, merged)
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
        )
    return count


def _make_soft_delete(table_name: str):
    """Create soft_delete classmethod."""
    @classmethod
    async def soft_delete(cls, db, id: str) -> bool:
        """Soft delete (set deleted_at timestamp)."""
        await db.save_entity(table_name, {
            'id': id,
            'deleted_at': _now_iso(),
            'updated_at': _now_iso(),
        })
        return True
    return soft_delete


def entity(table: str = None, history: bool = True):
    """
    Decorator to mark a dataclass as a database entity.
    
    Auto-adds CRUD methods - no separate store layer needed.
    
    Usage:
        @entity(table="projects")
        @dataclass
        class Project:
            name: str
            tags: List[str] = entity_field(default=None)
        
        # The entity IS the store:
        project = await Project.get(db, "123")
        projects = await Project.find(db, where="name = ?", params=("test",))
        await Project.save(db, {"name": "new"})
        await Project.delete(db, "123")
    """
    def decorator(cls):
        tbl = table or cls.__name__.lower()
        
        # Add entity metadata
        cls.__entity_table__ = tbl
        cls.__entity_history__ = history
        
        # Add dict-like access (obj['key'] and obj.key both work)
        _add_dict_access(cls)
        
        # Add methods (if not already defined)
        if not hasattr(cls, 'from_dict'):
            cls.from_dict = _make_from_dict(cls)
        
        if not hasattr(cls, 'get'):
            cls.get = _make_get(tbl, cls)
        
        if not hasattr(cls, 'find'):
            cls.find = _make_find(tbl, cls)
        
        if not hasattr(cls, 'save'):
            cls.save = _make_save(tbl, cls)
        
        # Alias create -> save for backward compatibility
        if not hasattr(cls, 'create'):
            cls.create = cls.save
        
        if not hasattr(cls, 'delete'):
            cls.delete = _make_delete(tbl)
        
        if not hasattr(cls, 'update'):
            cls.update = _make_update(tbl, cls)
        
        if not hasattr(cls, 'count'):
            cls.count = _make_count(tbl)
        
        if not hasattr(cls, 'soft_delete'):
            cls.soft_delete = _make_soft_delete(tbl)
        
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
