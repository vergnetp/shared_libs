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


def generate_crud(entities: list[Entity]) -> str:
    """Generate _gen/crud.py with generic CRUD operations."""
    return '''"""
Generic CRUD operations - AUTO-GENERATED from manifest.yaml
DO NOT EDIT - changes will be overwritten on regenerate
"""

from typing import Any, Optional, TypeVar
from datetime import datetime, timezone
import uuid

T = TypeVar("T")


class EntityCRUD:
    """Generic CRUD for any entity."""
    
    def __init__(self, table: str, soft_delete: bool = False):
        self.table = table
        self.soft_delete = soft_delete
    
    async def list(
        self, 
        db: Any, 
        skip: int = 0, 
        limit: int = 100,
        workspace_id: Optional[str] = None,
        include_deleted: bool = False,
    ) -> list[dict]:
        """List entities with pagination."""
        conditions = []
        params = []
        
        if workspace_id:
            conditions.append("[workspace_id] = ?")
            params.append(workspace_id)
        
        where_clause = " AND ".join(conditions) if conditions else None
        
        return await db.find_entities(
            self.table,
            where_clause=where_clause,
            params=tuple(params) if params else None,
            limit=limit,
            offset=skip,
            include_deleted=include_deleted if self.soft_delete else True,
        )
    
    async def get(self, db: Any, id: str, include_deleted: bool = False) -> Optional[dict]:
        """Get entity by ID."""
        return await db.get_entity(self.table, id, include_deleted=include_deleted if self.soft_delete else True)
    
    async def create(self, db: Any, data: Any) -> dict:
        """Create new entity."""
        now = datetime.now(timezone.utc).isoformat()
        entity_id = str(uuid.uuid4())
        
        values = data.model_dump(exclude_unset=True)
        values["id"] = entity_id
        values["created_at"] = now
        values["updated_at"] = now
        
        result = await db.save_entity(self.table, values)
        return result
    
    async def update(self, db: Any, id: str, data: Any) -> Optional[dict]:
        """Update entity."""
        # Get existing entity
        existing = await self.get(db, id)
        if not existing:
            return None
        
        values = data.model_dump(exclude_unset=True)
        if not values:
            return existing
        
        # Merge with existing
        updated = {**existing, **values}
        updated["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        result = await db.save_entity(self.table, updated)
        return result
    
    async def delete(self, db: Any, id: str) -> bool:
        """Delete entity (soft delete if configured)."""
        permanent = not self.soft_delete
        return await db.delete_entity(self.table, id, permanent=permanent)
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
from ...src.deps import get_db

router = APIRouter(prefix="/{table}", tags=["{table}"])
crud = EntityCRUD("{table}", soft_delete={entity.soft_delete})


@router.get("", response_model=list[{cls}Response])
async def list_{table}(
    db=Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    {"workspace_id: Optional[str] = None," if entity.workspace_scoped else ""}
):
    """List {table}."""
    return await crud.list(db, skip=skip, limit=limit{", workspace_id=workspace_id" if entity.workspace_scoped else ""})


@router.post("", response_model={cls}Response, status_code=201)
async def create_{entity.name}(data: {cls}Create, db=Depends(get_db)):
    """Create {entity.name}."""
    return await crud.create(db, data)


@router.get("/{{id}}", response_model={cls}Response)
async def get_{entity.name}(id: str, db=Depends(get_db)):
    """Get {entity.name} by ID."""
    entity = await crud.get(db, id)
    if not entity:
        raise HTTPException(404, "{cls} not found")
    return entity


@router.patch("/{{id}}", response_model={cls}Response)
async def update_{entity.name}(id: str, data: {cls}Update, db=Depends(get_db)):
    """Update {entity.name}."""
    entity = await crud.update(db, id, data)
    if not entity:
        raise HTTPException(404, "{cls} not found")
    return entity


@router.delete("/{{id}}", status_code=204)
async def delete_{entity.name}(id: str, db=Depends(get_db)):
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

from typing import AsyncGenerator
from backend.app_kernel.db import db_session_dependency, get_db_session

# Re-export kernel's db dependency
get_db = db_session_dependency

# For workers - use context manager
get_db_context = get_db_session


# =============================================================================
# Add your custom dependencies below
# =============================================================================

# Example:
# _my_service: Optional[MyService] = None
#
# def get_my_service() -> MyService:
#     if _my_service is None:
#         raise RuntimeError("Service not initialized")
#     return _my_service
'''


def generate_src_routes_init() -> str:
    """Generate src/routes/__init__.py stub."""
    return '''"""
Custom routes.

This file is YOUR code - never overwritten by generator.
Import and combine your custom routers here.
"""

from fastapi import APIRouter

router = APIRouter()

# Import and include your custom routes:
# from .chat import router as chat_router
# router.include_router(chat_router)
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
    manifest_path = Path(args.from_manifest)
    if not manifest_path.exists():
        print(f"Error: Manifest not found: {manifest_path}")
        sys.exit(1)
    
    # Determine output location
    if args.output:
        project_dir = Path(args.output)
    else:
        project_dir = Path(args.name)
    
    if project_dir.exists():
        print(f"Error: Directory already exists: {project_dir}")
        sys.exit(1)
    
    manifest = load_manifest(manifest_path)
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
    write_file(project_dir / "src" / "workers" / "__init__.py", "", overwrite=False)
    
    # Generate root files (only if not exists)
    write_file(project_dir / "main.py", generate_main(manifest), overwrite=False)
    write_file(project_dir / "config.py", generate_config(manifest), overwrite=False)
    
    print(f"\n✓ Created {project_dir}/")
    print(f"\nNext steps:")
    print(f"  cd {project_dir}")
    print(f"  # Add custom routes to src/routes/")
    print(f"  # Add workers to src/workers/")
    print(f"  # Edit manifest.yaml and run: appctl generate")


def cmd_generate(args):
    """Regenerate _gen/ from manifest."""
    manifest_path = Path("manifest.yaml")
    if not manifest_path.exists():
        print("Error: manifest.yaml not found in current directory")
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
    new_parser = subparsers.add_parser("new", help="Create new project")
    new_parser.add_argument("name", help="Project name")
    new_parser.add_argument("--from-manifest", required=True, help="Manifest file")
    new_parser.add_argument("--output", "-o", help="Output directory (default: current dir)")
    
    # generate
    gen_parser = subparsers.add_parser("generate", help="Regenerate from manifest")
    gen_parser.add_argument("--force", action="store_true", help="Force regenerate all files")
    gen_parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    
    args = parser.parse_args()
    
    if args.command == "new":
        cmd_new(args)
    elif args.command == "generate":
        cmd_generate(args)


if __name__ == "__main__":
    main()