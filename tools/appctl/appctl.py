#!/usr/bin/env python3
"""
appctl - App Kernel CLI Tool

Commands:
  new <name> --from-manifest <file>   Create new project from manifest
  generate                            Regenerate _gen/ from manifest
  add entity <name>                   Add new entity (updates manifest)
"""

import os
import sys
import yaml
import argparse
from pathlib import Path
from typing import Any
from dataclasses import dataclass


# =============================================================================
# Type Mappings
# =============================================================================

PYTHON_TYPES = {
    "string": "str",
    "text": "str",
    "int": "int",
    "float": "float",
    "bool": "bool",
    "datetime": "datetime",
    "json": "dict",
}

SQLITE_TYPES = {
    "string": "TEXT",
    "text": "TEXT",
    "int": "INTEGER",
    "float": "REAL",
    "bool": "INTEGER",
    "datetime": "TEXT",
    "json": "TEXT",
}

PYDANTIC_TYPES = {
    "string": "str",
    "text": "str",
    "int": "int",
    "float": "float",
    "bool": "bool",
    "datetime": "datetime",
    "json": "Dict[str, Any]",
}

# For dataclass entities (internal typed objects)
DATACLASS_TYPES = {
    "string": ("str", '""'),
    "text": ("str", '""'),
    "int": ("int", "0"),
    "float": ("float", "0.0"),
    "bool": ("bool", "False"),
    "datetime": ("Optional[str]", "None"),  # ISO string
    "json": ("Optional[Dict[str, Any]]", "None"),
}


# =============================================================================
# Manifest Loading
# =============================================================================

@dataclass
class Field:
    name: str
    type: str
    required: bool = False
    default: Any = None


@dataclass
class Entity:
    name: str
    fields: list[Field]
    workspace_scoped: bool = False
    soft_delete: bool = False
    
    @property
    def table_name(self) -> str:
        """Pluralize for table name."""
        if self.name.endswith('y'):
            return self.name[:-1] + 'ies'
        elif self.name.endswith('s'):
            return self.name + 'es'
        return self.name + 's'
    
    @property
    def class_name(self) -> str:
        """PascalCase for class name."""
        return ''.join(word.capitalize() for word in self.name.split('_'))


def load_manifest(path: Path) -> dict:
    """Load and parse manifest YAML."""
    with open(path) as f:
        return yaml.safe_load(f)


def parse_entities(manifest: dict) -> list[Entity]:
    """Parse entities from manifest."""
    entities = []
    for e in manifest.get("entities", []):
        fields = []
        for f in e.get("fields", []):
            fields.append(Field(
                name=f["name"],
                type=f.get("type", "string"),
                required=f.get("required", False),
                default=f.get("default"),
            ))
        entities.append(Entity(
            name=e["name"],
            fields=fields,
            workspace_scoped=e.get("workspace_scoped", False),
            soft_delete=e.get("soft_delete", False),
        ))
    return entities


# =============================================================================
# Code Generators
# =============================================================================

def generate_db_schema(entities: list[Entity], manifest: dict) -> str:
    """Generate _gen/db_schema.py."""
    lines = [
        '"""',
        'Database schema - AUTO-GENERATED from manifest.yaml',
        'DO NOT EDIT - changes will be overwritten on regenerate',
        '"""',
        '',
        'from typing import Any',
        '',
        '',
        'async def init_schema(db: Any) -> None:',
        '    """Initialize database schema. Called by kernel after DB connection."""',
        '',
    ]
    
    for entity in entities:
        table = entity.table_name
        lines.append(f'    # {entity.class_name}')
        lines.append(f'    await db.execute("""')
        lines.append(f'        CREATE TABLE IF NOT EXISTS {table} (')
        lines.append(f'            id TEXT PRIMARY KEY,')
        
        # Workspace scope
        if entity.workspace_scoped:
            lines.append(f'            workspace_id TEXT,')
        
        # Entity fields
        for field in entity.fields:
            sql_type = SQLITE_TYPES.get(field.type, "TEXT")
            default = ""
            # Only emit DEFAULT for non-standard values
            # SQLite defaults: INTEGER->NULL, TEXT->NULL, REAL->NULL
            if field.default is not None:
                if isinstance(field.default, str):
                    default = f" DEFAULT '{field.default}'"
                elif isinstance(field.default, bool):
                    default = f" DEFAULT {1 if field.default else 0}"
                else:
                    default = f" DEFAULT {field.default}"
            not_null = " NOT NULL" if field.required else ""
            lines.append(f'            {field.name} {sql_type}{not_null}{default},')
        
        # Timestamps
        lines.append(f'            created_at TEXT,')
        lines.append(f'            updated_at TEXT,')
        if entity.soft_delete:
            lines.append(f'            deleted_at TEXT,')
        
        # Remove trailing comma from last field
        lines[-1] = lines[-1].rstrip(',')
        lines.append(f'        )')
        lines.append(f'    """)')
        
        # Indexes
        if entity.workspace_scoped:
            lines.append(f'    await db.execute("CREATE INDEX IF NOT EXISTS idx_{table}_workspace ON {table}(workspace_id)")')
        
        lines.append('')
    
    return '\n'.join(lines)


def generate_schemas(entities: list[Entity]) -> str:
    """Generate _gen/schemas.py."""
    lines = [
        '"""',
        'Pydantic schemas - AUTO-GENERATED from manifest.yaml',
        'DO NOT EDIT - changes will be overwritten on regenerate',
        '"""',
        '',
        'from datetime import datetime',
        'from typing import Any, Dict, Optional',
        'from pydantic import BaseModel',
        '',
        '',
    ]
    
    for entity in entities:
        cls = entity.class_name
        
        # Base (shared fields)
        lines.append(f'class {cls}Base(BaseModel):')
        if not entity.fields:
            lines.append('    pass')
        else:
            for field in entity.fields:
                py_type = PYDANTIC_TYPES.get(field.type, "str")
                if field.required:
                    lines.append(f'    {field.name}: {py_type}')
                elif field.default is not None:
                    # Only emit explicit defaults
                    default_val = repr(field.default)
                    lines.append(f'    {field.name}: Optional[{py_type}] = {default_val}')
                else:
                    lines.append(f'    {field.name}: Optional[{py_type}] = None')
        lines.append('')
        
        # Create
        lines.append(f'class {cls}Create({cls}Base):')
        if entity.workspace_scoped:
            lines.append('    workspace_id: Optional[str] = None')
        else:
            lines.append('    pass')
        lines.append('')
        
        # Update (all optional)
        lines.append(f'class {cls}Update(BaseModel):')
        if not entity.fields:
            lines.append('    pass')
        else:
            for field in entity.fields:
                py_type = PYDANTIC_TYPES.get(field.type, "str")
                lines.append(f'    {field.name}: Optional[{py_type}] = None')
        lines.append('')
        
        # Response
        lines.append(f'class {cls}Response({cls}Base):')
        lines.append('    id: str')
        if entity.workspace_scoped:
            lines.append('    workspace_id: Optional[str] = None')
        lines.append('    created_at: Optional[datetime] = None')
        lines.append('    updated_at: Optional[datetime] = None')
        if entity.soft_delete:
            lines.append('    deleted_at: Optional[datetime] = None')
        lines.append('')
        lines.append(f'    class Config:')
        lines.append(f'        from_attributes = True')
        lines.append('')
        lines.append('')
    
    return '\n'.join(lines)


def generate_entities(entities: list[Entity]) -> str:
    """Generate _gen/entities.py with typed dataclasses."""
    lines = [
        '"""',
        'Typed entity dataclasses - AUTO-GENERATED from manifest.yaml',
        'DO NOT EDIT - changes will be overwritten on regenerate',
        '',
        'These provide type-safe entity access for internal code.',
        'Use Pydantic schemas (schemas.py) for API validation.',
        '"""',
        '',
        'from dataclasses import dataclass, fields, asdict',
        'from typing import Any, Dict, List, Optional',
        '',
        '',
    ]
    
    for entity in entities:
        cls = entity.class_name
        
        lines.append('@dataclass')
        lines.append(f'class {cls}:')
        lines.append(f'    """Typed entity for {entity.name}."""')
        lines.append('')
        
        # System fields
        lines.append('    # System fields')
        lines.append('    id: Optional[str] = None')
        if entity.workspace_scoped:
            lines.append('    workspace_id: Optional[str] = None')
        lines.append('    created_at: Optional[str] = None')
        lines.append('    updated_at: Optional[str] = None')
        lines.append('    created_by: Optional[str] = None')
        lines.append('    updated_by: Optional[str] = None')
        if entity.soft_delete:
            lines.append('    deleted_at: Optional[str] = None')
        
        # Required fields
        required_fields = [f for f in entity.fields if f.required]
        if required_fields:
            lines.append('')
            lines.append('    # Required fields')
            for field in required_fields:
                py_type, _ = DATACLASS_TYPES.get(field.type, ("str", '""'))
                lines.append(f'    {field.name}: {py_type} = None  # required')
        
        # Optional fields
        optional_fields = [f for f in entity.fields if not f.required]
        if optional_fields:
            lines.append('')
            lines.append('    # Optional fields')
            for field in optional_fields:
                py_type, py_default = DATACLASS_TYPES.get(field.type, ("str", '""'))
                if field.default is not None:
                    if isinstance(field.default, str):
                        default_val = f'"{field.default}"'
                    elif isinstance(field.default, bool):
                        default_val = str(field.default)
                    else:
                        default_val = str(field.default)
                else:
                    default_val = py_default
                if not py_type.startswith("Optional"):
                    py_type = f"Optional[{py_type}]"
                lines.append(f'    {field.name}: {py_type} = {default_val}')
        
        # from_dict method
        lines.append('')
        lines.append('    @classmethod')
        lines.append(f"    def from_dict(cls, data: Dict[str, Any]) -> '{cls}':")
        lines.append('        """Create from dict, filtering to known fields."""')
        lines.append('        if data is None:')
        lines.append('            return None')
        lines.append('        field_names = {f.name for f in fields(cls)}')
        lines.append('        filtered = {k: v for k, v in data.items() if k in field_names}')
        lines.append('        return cls(**filtered)')
        
        lines.append('')
        lines.append('')
    
    # __all__ export
    class_names = [e.class_name for e in entities]
    lines.append('__all__ = [')
    for name in class_names:
        lines.append(f'    "{name}",')
    lines.append(']')
    
    return '\n'.join(lines)


def generate_crud(entities: list[Entity]) -> str:
    """Generate _gen/crud.py with generic CRUD operations."""
    return '''"""
Generic CRUD operations - AUTO-GENERATED from manifest.yaml
DO NOT EDIT - changes will be overwritten on regenerate

Usage in stores:
    from .._gen.crud import EntityCRUD
    
    class MyStore:
        def __init__(self, db):
            self.db = db
            self._crud = EntityCRUD("my_table")
        
        async def create(self, data: dict) -> dict:
            return await self._crud.create(self.db, data)
"""

from typing import Any, Optional, List
from datetime import datetime, timezone
import uuid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


def _to_dict(data: Any) -> dict:
    """Convert pydantic model or dict to dict."""
    if hasattr(data, "model_dump"):
        return data.model_dump(exclude_unset=True)
    return dict(data)


class EntityCRUD:
    """
    Generic CRUD for any entity table.
    
    Uses the databases library entity methods:
    - db.find_entities() for list/search
    - db.get_entity() for get by id
    - db.save_entity() for create/update
    - db.delete_entity() for delete
    """
    
    def __init__(self, table: str, soft_delete: bool = False):
        self.table = table
        self.soft_delete = soft_delete
    
    async def list(
        self, 
        db: Any, 
        where_clause: str = None,
        params: tuple = None,
        order_by: str = None,
        limit: int = 100,
        offset: int = 0,
        workspace_id: str = None,
        include_deleted: bool = False,
    ) -> List[dict]:
        """List entities with optional filtering."""
        conditions = []
        all_params = []
        
        if workspace_id:
            conditions.append("[workspace_id] = ?")
            all_params.append(workspace_id)
        
        if where_clause:
            conditions.append(f"({where_clause})")
            if params:
                all_params.extend(params)
        
        final_where = " AND ".join(conditions) if conditions else None
        final_params = tuple(all_params) if all_params else None
        
        return await db.find_entities(
            self.table,
            where_clause=final_where,
            params=final_params,
            order_by=order_by or "[created_at] DESC",
            limit=limit,
            offset=offset,
            include_deleted=include_deleted if self.soft_delete else True,
        )
    
    async def get(self, db: Any, entity_id: str) -> Optional[dict]:
        """Get entity by ID."""
        return await db.get_entity(self.table, entity_id)
    
    async def create(self, db: Any, data: Any, entity_id: str = None) -> dict:
        """Create new entity from dict or pydantic model."""
        values = _to_dict(data)
        values["id"] = entity_id or _uuid()
        values["created_at"] = _now()
        values["updated_at"] = _now()
        
        return await db.save_entity(self.table, values)
    
    async def update(self, db: Any, entity_id: str, data: Any) -> Optional[dict]:
        """Update entity. Merges with existing."""
        existing = await self.get(db, entity_id)
        if not existing:
            return None
        
        updates = _to_dict(data)
        if not updates:
            return existing
        
        # Merge
        for k, v in updates.items():
            existing[k] = v
        existing["updated_at"] = _now()
        
        return await db.save_entity(self.table, existing)
    
    async def save(self, db: Any, entity: dict) -> dict:
        """Save entity (upsert). Entity must have 'id'."""
        entity["updated_at"] = _now()
        if "created_at" not in entity:
            entity["created_at"] = _now()
        return await db.save_entity(self.table, entity)
    
    async def delete(self, db: Any, entity_id: str, permanent: bool = None) -> bool:
        """Delete entity. Uses soft_delete setting unless permanent specified."""
        is_permanent = permanent if permanent is not None else not self.soft_delete
        return await db.delete_entity(self.table, entity_id, permanent=is_permanent)
    
    async def find_one(
        self,
        db: Any,
        where_clause: str,
        params: tuple = None,
    ) -> Optional[dict]:
        """Find single entity matching criteria."""
        results = await db.find_entities(
            self.table,
            where_clause=where_clause,
            params=params,
            limit=1,
        )
        return results[0] if results else None
    
    async def count(
        self,
        db: Any,
        where_clause: str = None,
        params: tuple = None,
        workspace_id: str = None,
    ) -> int:
        """Count entities matching criteria."""
        conditions = []
        all_params = []
        
        if workspace_id:
            conditions.append("[workspace_id] = ?")
            all_params.append(workspace_id)
        
        if where_clause:
            conditions.append(f"({where_clause})")
            if params:
                all_params.extend(params)
        
        final_where = " AND ".join(conditions) if conditions else None
        final_params = tuple(all_params) if all_params else None
        
        return await db.count_entities(
            self.table,
            where_clause=final_where,
            params=final_params,
        )
'''


def generate_entity_router(entity: Entity) -> str:
    """Generate _gen/routes/{entity}.py."""
    cls = entity.class_name
    table = entity.table_name
    
    return f'''"""
{cls} CRUD routes - AUTO-GENERATED from manifest.yaml
DO NOT EDIT - changes will be overwritten on regenerate

For custom logic, create src/routes/{entity.name}.py
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from ..schemas import {cls}Create, {cls}Update, {cls}Response
from ..crud import EntityCRUD

# Import db dependency from src (allows customization)
from ...src.deps import db_connection

router = APIRouter(prefix="/{table}", tags=["{table}"])
crud = EntityCRUD("{table}", soft_delete={entity.soft_delete})


@router.get("", response_model=list[{cls}Response])
async def list_{table}(
    db=Depends(db_connection),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    {"workspace_id: Optional[str] = None," if entity.workspace_scoped else ""}
):
    """List {table}."""
    return await crud.list(db, skip=skip, limit=limit{", workspace_id=workspace_id" if entity.workspace_scoped else ""})


@router.post("", response_model={cls}Response, status_code=201)
async def create_{entity.name}(data: {cls}Create, db=Depends(db_connection)):
    """Create {entity.name}."""
    return await crud.create(db, data)


@router.get("/{{id}}", response_model={cls}Response)
async def get_{entity.name}(id: str, db=Depends(db_connection)):
    """Get {entity.name} by ID."""
    entity = await crud.get(db, id)
    if not entity:
        raise HTTPException(404, "{cls} not found")
    return entity


@router.patch("/{{id}}", response_model={cls}Response)
async def update_{entity.name}(id: str, data: {cls}Update, db=Depends(db_connection)):
    """Update {entity.name}."""
    entity = await crud.update(db, id, data)
    if not entity:
        raise HTTPException(404, "{cls} not found")
    return entity


@router.delete("/{{id}}", status_code=204)
async def delete_{entity.name}(id: str, db=Depends(db_connection)):
    """Delete {entity.name}."""
    await crud.delete(db, id)
'''


def generate_routes_init(entities: list[Entity]) -> str:
    """Generate _gen/routes/__init__.py."""
    lines = [
        '"""',
        'Generated routes - AUTO-GENERATED from manifest.yaml',
        'DO NOT EDIT - changes will be overwritten on regenerate',
        '"""',
        '',
        'from fastapi import APIRouter',
        '',
    ]
    
    # Import all entity routers
    for entity in entities:
        lines.append(f'from .{entity.name} import router as {entity.name}_router')
    
    lines.append('')
    lines.append('# Combined router for all generated CRUD endpoints')
    lines.append('router = APIRouter()')
    lines.append('')
    
    for entity in entities:
        lines.append(f'router.include_router({entity.name}_router)')
    
    return '\n'.join(lines)


def generate_gen_init() -> str:
    """Generate _gen/__init__.py."""
    return '''"""
Generated code - AUTO-GENERATED from manifest.yaml
DO NOT EDIT - changes will be overwritten on regenerate

For custom logic, put code in src/
"""

from .db_schema import init_schema
from .schemas import *
from .entities import *
from .crud import EntityCRUD
from .routes import router as gen_router

__all__ = ["init_schema", "EntityCRUD", "gen_router"]
'''


def generate_src_deps() -> str:
    """Generate src/deps.py stub."""
    return '''"""
Application dependencies.

This file is YOUR code - never overwritten by generator.
Add custom dependencies here.
"""

from backend.app_kernel.db import db_connection, get_db_connection

__all__ = ["db_connection", "get_db_connection"]


# =============================================================================
# Add your custom dependencies below
# =============================================================================
'''


def generate_src_routes_init() -> str:
    """Generate src/routes/__init__.py stub."""
    return '''"""
Custom routes.

This file is YOUR code - never overwritten by generator.
Import and combine your custom routers here.
"""

from fastapi import APIRouter
from .custom import router as custom_router

router = APIRouter()
router.include_router(custom_router)
'''


def generate_src_routes_custom() -> str:
    """Generate src/routes/custom.py example."""
    return '''"""
Custom routes - example file.

Add your business logic routes here.
"""

from fastapi import APIRouter, Depends, HTTPException
from ..deps import db_connection

router = APIRouter(prefix="/custom", tags=["custom"])


@router.get("/hello")
async def hello():
    """Example endpoint."""
    return {"message": "Hello from custom route!"}


@router.get("/items/{item_id}")
async def get_item(item_id: str, db=Depends(db_connection)):
    """Example: fetch from database."""
    item = await db.get_entity("items", item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.post("/items")
async def create_item(name: str, db=Depends(db_connection)):
    """Example: save to database."""
    item = await db.save_entity("items", {"name": name})
    return item
'''


def generate_src_workers_init() -> str:
    """Generate src/workers/__init__.py stub."""
    return '''"""
Custom background tasks.

This file is YOUR code - never overwritten by generator.
Register your task functions here.
"""

from .tasks import process_item

# Task registry - imported by worker.py
tasks = {
    "process_item": process_item,
}
'''


def generate_src_workers_tasks() -> str:
    """Generate src/workers/tasks.py example."""
    return '''"""
Background tasks - example file.

Tasks are async functions that run in the worker process.
They receive a job context and any arguments passed when enqueued.
"""

from ..deps import get_db_connection
from backend.app_kernel import get_logger

logger = get_logger()


async def process_item(ctx, item_id: str):
    """
    Example background task.
    
    Args:
        ctx: Job context with metadata (job_id, attempt, etc.)
        item_id: The item to process
    
    Usage (from a route):
        from backend.app_kernel.jobs import get_job_client
        client = get_job_client()
        await client.enqueue("process_item", item_id="123")
    """
    logger.info(f"Processing item {item_id}", extra={"job_id": ctx.job_id})
    
    async with get_db_connection() as db:
        # Fetch item
        item = await db.get_entity("items", item_id)
        if not item:
            logger.error(f"Item not found: {item_id}")
            return {"status": "error", "message": "Item not found"}
        
        # Do some processing...
        item["processed"] = True
        await db.save_entity("items", item)
        
        logger.info(f"Item {item_id} processed successfully")
        return {"status": "ok", "item_id": item_id}
'''


def generate_worker(manifest: dict) -> str:
    """Generate worker.py."""
    name = manifest.get("name", "myapp")
    
    return f'''#!/usr/bin/env python3
"""
Background worker for {name}.

Run with: python -m services.{name}.worker
"""

import asyncio
from .config import settings
from .src.workers import tasks


async def init():
    """Initialize app dependencies."""
    settings.ensure_data_dir()


async def shutdown():
    """Cleanup."""
    pass


if __name__ == "__main__":
    from backend.app_kernel.jobs import run_worker
    
    if not tasks:
        print("No tasks registered in src/workers/__init__.py")
        print("Add tasks like: tasks['my_task'] = my_function")
    else:
        asyncio.run(run_worker(
            tasks=tasks,
            init_app=init,
            shutdown_app=shutdown,
        ))
'''


def generate_main(manifest: dict) -> str:
    """Generate main.py (only if not exists)."""
    name = manifest.get("name", "myapp")
    
    return f'''"""
Application entry point.

This file is generated ONCE - safe to customize after creation.
"""

from backend.app_kernel import create_service, ServiceConfig
from ._gen import init_schema, gen_router
from .src.routes import router as custom_router
from .config import settings


def _build_config() -> ServiceConfig:
    """Build kernel configuration from settings."""
    # Ensure data directory exists
    settings.ensure_data_dir()
    
    return ServiceConfig(
        # Auth
        jwt_secret=settings.jwt_secret,
        
        # Database
        database_name=settings.database_path,
        database_type=settings.database_type,
        
        # Redis
        redis_url=settings.redis_url,
    )


app = create_service(
    name="{name}",
    config=_build_config(),
    schema_init=init_schema,
    routers=[gen_router, custom_router],
)
'''


def generate_config(manifest: dict) -> str:
    """Generate config.py (only if not exists)."""
    name = manifest.get("name", "myapp")
    db = manifest.get("database", {})
    redis = manifest.get("redis", {})
    auth = manifest.get("auth", {})
    
    def parse_env_var(value: str, required_name: str) -> tuple[str, bool]:
        """Parse ${VAR} or ${VAR:-default} syntax. Returns (code, is_path)."""
        if not value:
            return f'os.environ["{required_name}"]', False
        if value.startswith("${"):
            var_part = value[2:-1]  # Remove ${ and }
            if ":-" in var_part:
                var_name, default = var_part.split(":-", 1)
                if default.startswith("./"):
                    return f'os.getenv("{var_name}") or str(SERVICE_DIR / "{default[2:]}")', True
                return f'os.getenv("{var_name}", "{default}")', False
            else:
                return f'os.environ["{var_part}"]', False
        elif value.startswith("./"):
            return f'str(SERVICE_DIR / "{value[2:]}")', True
        return f'"{value}"', False
    
    db_path = db.get("path", f"./data/{name}.db")
    db_path_code, _ = parse_env_var(db_path, "DATABASE_PATH")
    
    redis_url = redis.get("url", "")
    redis_url_code, _ = parse_env_var(redis_url, "REDIS_URL")
    
    jwt_secret = auth.get("jwt_secret", "")
    jwt_secret_code, _ = parse_env_var(jwt_secret, "JWT_SECRET")
    
    return f'''"""
Application configuration.

This file is generated ONCE - safe to customize after creation.
"""

import os
from pathlib import Path
from dataclasses import dataclass

# Service directory (where this file lives)
SERVICE_DIR = Path(__file__).parent


@dataclass(frozen=True)
class Settings:
    """Application settings from environment."""
    
    # Database
    database_path: str = {db_path_code}
    database_type: str = "{db.get('type', 'sqlite')}"
    
    # Redis
    redis_url: str = {redis_url_code}
    
    # Auth
    jwt_secret: str = {jwt_secret_code}
    
    @property
    def database_name(self) -> str:
        """Extract database name from path."""
        return Path(self.database_path).stem
    
    def ensure_data_dir(self):
        """Create data directory if needed."""
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
'''


# =============================================================================
# File Writing
# =============================================================================

def write_file(path: Path, content: str, overwrite: bool = True) -> bool:
    """Write file, optionally skipping if exists."""
    if path.exists() and not overwrite:
        print(f"  ⊘ {path} (exists - skipped)")
        return False
    
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    
    action = "updated" if path.exists() else "created"
    print(f"  ✓ {path} ({action})")
    return True


# =============================================================================
# Commands
# =============================================================================

def cmd_new(args):
    """Create new project from manifest."""
    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"Error: Manifest not found: {manifest_path}")
        sys.exit(1)
    
    manifest = load_manifest(manifest_path)
    name = manifest.get("name", manifest_path.stem)
    
    # Determine output location
    if args.output:
        project_dir = Path(args.output)
    else:
        project_dir = Path("services") / name
    
    if project_dir.exists():
        print(f"Error: Directory already exists: {project_dir}")
        sys.exit(1)
    
    entities = parse_entities(manifest)
    
    print(f"Creating {project_dir} from {manifest_path}...")
    
    # Create directory structure
    project_dir.mkdir()
    (project_dir / "_gen" / "routes").mkdir(parents=True)
    (project_dir / "src" / "routes").mkdir(parents=True)
    (project_dir / "src" / "workers").mkdir(parents=True)
    
    # Copy manifest
    write_file(project_dir / "manifest.yaml", manifest_path.read_text())
    
    # Generate _gen/ (always overwrite)
    write_file(project_dir / "_gen" / "__init__.py", generate_gen_init())
    write_file(project_dir / "_gen" / "db_schema.py", generate_db_schema(entities, manifest))
    write_file(project_dir / "_gen" / "schemas.py", generate_schemas(entities))
    write_file(project_dir / "_gen" / "crud.py", generate_crud(entities))
    write_file(project_dir / "_gen" / "entities.py", generate_entities(entities))
    write_file(project_dir / "_gen" / "routes" / "__init__.py", generate_routes_init(entities))
    for entity in entities:
        write_file(
            project_dir / "_gen" / "routes" / f"{entity.name}.py",
            generate_entity_router(entity)
        )
    
    # Generate src/ stubs (only if not exists)
    write_file(project_dir / "src" / "__init__.py", "", overwrite=False)
    write_file(project_dir / "src" / "deps.py", generate_src_deps(), overwrite=False)
    write_file(project_dir / "src" / "routes" / "__init__.py", generate_src_routes_init(), overwrite=False)
    write_file(project_dir / "src" / "routes" / "custom.py", generate_src_routes_custom(), overwrite=False)
    write_file(project_dir / "src" / "workers" / "__init__.py", generate_src_workers_init(), overwrite=False)
    write_file(project_dir / "src" / "workers" / "tasks.py", generate_src_workers_tasks(), overwrite=False)
    
    # Generate root files (only if not exists)
    write_file(project_dir / "main.py", generate_main(manifest), overwrite=False)
    write_file(project_dir / "config.py", generate_config(manifest), overwrite=False)
    write_file(project_dir / "worker.py", generate_worker(manifest), overwrite=False)
    
    print(f"\n✓ Created {project_dir}/")
    print(f"\nNext steps:")
    print(f"  cd {project_dir}")
    print(f"  # Add custom routes to src/routes/")
    print(f"  # Add workers to src/workers/")
    print(f"  # Edit manifest.yaml and run: appctl generate")


def cmd_generate(args):
    """Regenerate _gen/ from manifest."""
    manifest_path = Path(args.manifest) if args.manifest else Path("manifest.yaml")
    if not manifest_path.exists():
        print(f"Error: Manifest not found: {manifest_path}")
        sys.exit(1)
    
    manifest = load_manifest(manifest_path)
    entities = parse_entities(manifest)
    
    print("Regenerating from manifest.yaml...")
    
    gen_dir = Path("_gen")
    
    # Always regenerate _gen/
    write_file(gen_dir / "__init__.py", generate_gen_init())
    write_file(gen_dir / "db_schema.py", generate_db_schema(entities, manifest))
    write_file(gen_dir / "schemas.py", generate_schemas(entities))
    write_file(gen_dir / "crud.py", generate_crud(entities))
    write_file(gen_dir / "entities.py", generate_entities(entities))
    write_file(gen_dir / "routes" / "__init__.py", generate_routes_init(entities))
    for entity in entities:
        write_file(gen_dir / "routes" / f"{entity.name}.py", generate_entity_router(entity))
    
    # Skip src/
    print("  ⊘ src/* (your code - preserved)")
    
    # Optionally force regenerate root files
    if args.force:
        write_file(Path("main.py"), generate_main(manifest))
        write_file(Path("config.py"), generate_config(manifest))
    else:
        print("  ⊘ main.py (exists - skipped, use --force to regenerate)")
        print("  ⊘ config.py (exists - skipped, use --force to regenerate)")
    
    print("\n✓ Done!")


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="App Kernel CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # new
    new_parser = subparsers.add_parser("new", help="Create new project from manifest")
    new_parser.add_argument("manifest", help="Manifest file path")
    new_parser.add_argument("--output", "-o", help="Output directory (default: services/{name})")
    
    # generate
    gen_parser = subparsers.add_parser("generate", help="Regenerate _gen/ from manifest.yaml in current dir")
    gen_parser.add_argument("manifest", nargs="?", help="Manifest file (default: manifest.yaml)")
    gen_parser.add_argument("--force", "-f", action="store_true", help="Force regenerate main.py and config.py")
    
    args = parser.parse_args()
    
    if args.command == "new":
        cmd_new(args)
    elif args.command == "generate":
        cmd_generate(args)


if __name__ == "__main__":
    main()