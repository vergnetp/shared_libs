#!/usr/bin/env python3
"""
appctl - Application scaffold generator for app_kernel services.

Usage:
    appctl new myapp                     # Interactive mode
    appctl new myapp --from-manifest app.manifest.yaml
    appctl new myapp --db postgres --redis --tasks process_order,send_email
    appctl manifest myapp                # Generate manifest only
    
Output:
    myapp/
    ├── __init__.py
    ├── main.py
    ├── config.py
    ├── db_schema.py
    ├── schemas.py
    ├── tasks.py           (if tasks defined)
    ├── routes/
    │   ├── __init__.py
    │   └── {entity}.py    (for each entity)
    ├── app.manifest.yaml
    ├── Dockerfile
    ├── docker-compose.yml
    ├── requirements.txt
    └── .env.example
"""

import argparse
import os
import sys
from pathlib import Path
from typing import List, Optional

from manifest_schema import (
    AppManifest,
    DatabaseConfig,
    RedisConfig,
    AuthConfig,
    CorsConfig,
    EntityConfig,
    EntityField,
)
import templates


# =============================================================================
# Helpers
# =============================================================================

def to_pascal(name: str) -> str:
    """Convert snake_case to PascalCase."""
    return "".join(word.capitalize() for word in name.split("_"))


def to_plural(name: str) -> str:
    """Simple pluralization."""
    if name.endswith("y"):
        return name[:-1] + "ies"
    elif name.endswith("s"):
        return name + "es"
    return name + "s"


def field_to_sql_type(field_type: str, db_type: str = "sqlite") -> str:
    """Convert field type to SQL type."""
    mapping = {
        "string": "TEXT",
        "text": "TEXT",
        "int": "INTEGER",
        "float": "REAL",
        "bool": "INTEGER",  # SQLite uses INTEGER for bool
        "datetime": "TEXT",  # ISO format
        "json": "TEXT",      # JSON string
    }
    return mapping.get(field_type, "TEXT")


def field_to_python_type(field_type: str) -> str:
    """Convert field type to Python type hint."""
    mapping = {
        "string": "str",
        "text": "str",
        "int": "int",
        "float": "float",
        "bool": "bool",
        "datetime": "datetime",
        "json": "Any",
    }
    return mapping.get(field_type, "str")


# =============================================================================
# Generators
# =============================================================================

class ScaffoldGenerator:
    """Generates app scaffold from manifest."""
    
    def __init__(self, manifest: AppManifest, output_dir: Path):
        self.manifest = manifest
        self.output_dir = output_dir
        self.name = manifest.name
        self.name_upper = manifest.name.upper().replace("-", "_")
    
    def generate(self):
        """Generate all scaffold files."""
        # Create directories
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "routes").mkdir(exist_ok=True)
        
        # Generate files
        self._generate_init()
        self._generate_config()
        self._generate_main()
        self._generate_db_schema()
        self._generate_schemas()
        self._generate_routes()
        
        if self.manifest.tasks:
            self._generate_tasks()
        
        self._generate_dockerfile()
        self._generate_docker_compose()
        self._generate_requirements()
        self._generate_env_example()
        self._generate_manifest()
        
        print(f"\n✅ Generated {self.name}/ scaffold")
        print(f"   {len(list(self.output_dir.rglob('*')))} files created")
        print(f"\nNext steps:")
        print(f"   cd {self.name}")
        print(f"   cp .env.example .env")
        print(f"   # Edit .env with your secrets")
        print(f"   uvicorn {self.name}.main:app --reload")
    
    def _write(self, filename: str, content: str):
        """Write file to output directory."""
        path = self.output_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        print(f"   Created {filename}")
    
    def _generate_init(self):
        """Generate __init__.py."""
        content = templates.INIT_PY.format(
            name=self.name,
            description=self.manifest.description or f"{self.name} service",
            version=self.manifest.version,
        )
        self._write("__init__.py", content)
    
    def _generate_config(self):
        """Generate config.py."""
        m = self.manifest
        
        db_port = m.database.port
        if db_port is None:
            db_port = {"sqlite": 0, "postgres": 5432, "mysql": 3306}.get(m.database.type, 5432)
        
        content = templates.CONFIG_PY.format(
            name=self.name,
            name_upper=self.name_upper,
            version=m.version,
            host=m.host,
            port=m.port,
            debug_env=m.debug_env,
            db_type=m.database.type,
            db_name=m.database.name,
            db_host=m.database.host,
            db_port=db_port if db_port else "None",
            db_user=f'"{m.database.user}"' if m.database.user else "None",
            db_password_env=m.database.password_env or "DATABASE_PASSWORD",
            redis_url_env=m.redis.url_env,
            auth_enabled=str(m.auth.enabled),
            allow_signup=str(m.auth.allow_signup),
            jwt_secret_env=m.auth.jwt_secret_env,
            jwt_expiry=m.auth.jwt_expiry_hours,
            cors_origins_default=",".join(m.cors.origins),
        )
        self._write("config.py", content)
    
    def _generate_main(self):
        """Generate main.py."""
        m = self.manifest
        
        # Tasks import
        tasks_import = ""
        tasks_dict = ""
        tasks_arg = "tasks=None,"
        if m.tasks:
            tasks_import = "from .tasks import TASKS"
            tasks_dict = "tasks = TASKS"
            tasks_arg = "tasks=tasks,"
        
        # Routes import
        route_imports = []
        routers_list = []
        for entity in m.entities:
            if entity.generate_routes:
                plural = to_plural(entity.name)
                route_imports.append(f"from .routes.{plural} import router as {plural}_router")
                routers_list.append(f"            {plural}_router,")
        
        routes_import = "\n".join(route_imports) if route_imports else ""
        routers_list_str = "\n".join(routers_list) if routers_list else "            # Add your routers here"
        
        # Redis health check
        redis_health_check = ""
        redis_health_arg = ""
        if m.redis.enabled:
            redis_health_check = '''
async def check_redis() -> Tuple[bool, str]:
    """Health check for Redis connection."""
    try:
        from backend.app_kernel import get_kernel
        # Redis check via kernel
        return True, "redis connected"
    except Exception as e:
        return False, f"redis error: {e}"
'''
            redis_health_arg = ", check_redis"
        
        # Redis prefix
        redis_prefix = m.redis.key_prefix or f"{self.name.replace('-', '_')}:"
        
        content = templates.MAIN_PY.format(
            name=self.name,
            description=m.description or f"{self.name} service",
            tasks_import=tasks_import,
            routes_import=routes_import,
            redis_health_check=redis_health_check,
            redis_prefix=redis_prefix,
            cors_credentials=str(m.cors.credentials),
            tasks_dict=tasks_dict,
            routers_list=routers_list_str,
            tasks_arg=tasks_arg,
            redis_health_arg=redis_health_arg,
        )
        self._write("main.py", content)
    
    def _generate_db_schema(self):
        """Generate db_schema.py."""
        m = self.manifest
        
        # Build table list for docstring
        table_list = []
        for entity in m.entities:
            table_list.append(f"- {to_plural(entity.name)}")
        table_list_str = "\n".join(table_list) if table_list else "- (no entities defined)"
        
        # Build schema statements
        statements = []
        for entity in m.entities:
            stmt = self._generate_entity_schema(entity)
            statements.append(stmt)
        
        if not statements:
            statements.append("    # No entities defined - add your tables here")
            statements.append("    pass")
        
        schema_statements = "\n\n".join(statements)
        
        content = templates.DB_SCHEMA_PY.format(
            name=self.name,
            table_list=table_list_str,
            schema_statements=schema_statements,
        )
        self._write("db_schema.py", content)
    
    def _generate_entity_schema(self, entity: EntityConfig) -> str:
        """Generate schema SQL for an entity."""
        table_name = to_plural(entity.name)
        
        # Build columns
        columns = ["        id TEXT PRIMARY KEY"]
        
        for field in entity.fields:
            sql_type = field_to_sql_type(field.type)
            nullable = "" if field.required else ""  # SQLite doesn't enforce NOT NULL well
            default = ""
            if field.default is not None:
                if field.type in ("string", "text", "json"):
                    default = f" DEFAULT '{field.default}'"
                else:
                    default = f" DEFAULT {field.default}"
            columns.append(f"        {field.name} {sql_type}{nullable}{default}")
        
        if entity.workspace_scoped:
            columns.append("        workspace_id TEXT")
        
        columns.extend([
            "        created_at TEXT",
            "        updated_at TEXT",
        ])
        
        if entity.soft_delete:
            columns.append("        deleted_at TEXT")
        
        columns_str = ",\n".join(columns)
        
        # Build indexes
        indexes = []
        if entity.workspace_scoped:
            indexes.append(f'    await db.execute("CREATE INDEX IF NOT EXISTS idx_{table_name}_workspace ON {table_name}(workspace_id)")')
        
        indexes_str = "\n".join(indexes) if indexes else ""
        
        return f'''    # {to_pascal(entity.name)}
    await db.execute("""
        CREATE TABLE IF NOT EXISTS {table_name} (
{columns_str}
        )
    """)
{indexes_str}'''
    
    def _generate_schemas(self):
        """Generate schemas.py with Pydantic models."""
        m = self.manifest
        
        entity_schemas = []
        for entity in m.entities:
            schema = self._generate_entity_pydantic(entity)
            entity_schemas.append(schema)
        
        if not entity_schemas:
            entity_schemas.append("# Add your Pydantic schemas here")
        
        content = templates.SCHEMAS_PY.format(
            name=self.name,
            entity_schemas="\n\n".join(entity_schemas),
        )
        self._write("schemas.py", content)
    
    def _generate_entity_pydantic(self, entity: EntityConfig) -> str:
        """Generate Pydantic schemas for an entity."""
        pascal = to_pascal(entity.name)
        
        # Create fields
        create_fields = []
        for field in entity.fields:
            py_type = field_to_python_type(field.type)
            if not field.required:
                py_type = f"Optional[{py_type}]"
                default = f" = {repr(field.default)}" if field.default is not None else " = None"
            else:
                default = ""
            create_fields.append(f"    {field.name}: {py_type}{default}")
        
        create_fields_str = "\n".join(create_fields) if create_fields else "    pass"
        
        # Update fields (all optional)
        update_fields = []
        for field in entity.fields:
            py_type = field_to_python_type(field.type)
            update_fields.append(f"    {field.name}: Optional[{py_type}] = None")
        
        update_fields_str = "\n".join(update_fields) if update_fields else "    pass"
        
        # Response fields
        response_fields = ["    id: str"]
        for field in entity.fields:
            py_type = field_to_python_type(field.type)
            if not field.required:
                py_type = f"Optional[{py_type}]"
            response_fields.append(f"    {field.name}: {py_type}")
        
        if entity.workspace_scoped:
            response_fields.append("    workspace_id: Optional[str] = None")
        
        response_fields.extend([
            "    created_at: Optional[str] = None",
            "    updated_at: Optional[str] = None",
        ])
        
        response_fields_str = "\n".join(response_fields)
        
        return f'''# {pascal} schemas
class {pascal}Create(BaseModel):
{create_fields_str}


class {pascal}Update(BaseModel):
{update_fields_str}


class {pascal}Response(BaseModel):
{response_fields_str}
    
    class Config:
        from_attributes = True'''
    
    def _generate_routes(self):
        """Generate route files."""
        m = self.manifest
        
        route_imports = []
        route_exports = []
        
        for entity in m.entities:
            if entity.generate_routes:
                self._generate_entity_route(entity)
                plural = to_plural(entity.name)
                route_imports.append(f"from .{plural} import router as {plural}_router")
                route_exports.append(f'    "{plural}_router",')
        
        # Generate routes/__init__.py
        content = templates.ROUTES_INIT_PY.format(
            name=self.name,
            route_imports="\n".join(route_imports) if route_imports else "# No routes generated",
            route_exports="\n".join(route_exports) if route_exports else '    # No routes',
        )
        self._write("routes/__init__.py", content)
    
    def _generate_entity_route(self, entity: EntityConfig):
        """Generate route file for an entity."""
        pascal = to_pascal(entity.name)
        plural = to_plural(entity.name)
        
        # Workspace handling
        if entity.workspace_scoped:
            workspace_import = "from backend.app_kernel.access import require_workspace_member"
            workspace_param = "    workspace_id: str,"
            workspace_field = '        "workspace_id": workspace_id,'
            workspace_filter = '"workspace_id": workspace_id, '
            workspace_check = f'''    if entity.get("workspace_id") != workspace_id:
        raise HTTPException(status_code=403, detail="Access denied")'''
        else:
            workspace_import = ""
            workspace_param = ""
            workspace_field = ""
            workspace_filter = ""
            workspace_check = ""
        
        # Soft delete filter
        deleted_filter = '"deleted_at": None' if entity.soft_delete else ""
        
        # Delete implementation
        if entity.soft_delete:
            delete_impl = f'''    from datetime import datetime, timezone
    
    entity = await db.get_entity("{plural}", id)
    if not entity:
        raise HTTPException(status_code=404, detail="{pascal} not found")
{workspace_check}
    
    entity["deleted_at"] = datetime.now(timezone.utc).isoformat()
    await db.save_entity("{plural}", entity)'''
        else:
            delete_impl = f'''    entity = await db.get_entity("{plural}", id)
    if not entity:
        raise HTTPException(status_code=404, detail="{pascal} not found")
{workspace_check}
    
    await db.delete_entity("{plural}", id)'''
        
        schema_classes = f"{pascal}Create, {pascal}Update, {pascal}Response"
        
        content = templates.ENTITY_ROUTE_PY.format(
            entity_name=entity.name,
            entity_name_plural=plural,
            entity_name_pascal=pascal,
            workspace_import=workspace_import,
            workspace_param=workspace_param,
            workspace_field=workspace_field,
            workspace_filter=workspace_filter,
            workspace_check=workspace_check,
            deleted_filter=deleted_filter,
            delete_impl=delete_impl,
            schema_classes=schema_classes,
        )
        self._write(f"routes/{plural}.py", content)
    
    def _generate_tasks(self):
        """Generate tasks.py."""
        m = self.manifest
        
        # Generate handler stubs
        handlers = []
        registry_entries = []
        
        for task in m.tasks:
            handler = f'''async def {task}(payload: Dict[str, Any], ctx: JobContext) -> Dict[str, Any]:
    """
    Handler for {task} task.
    
    Args:
        payload: Task payload
        ctx: Job context (job_id, attempt, user_id, etc.)
    
    Returns:
        Result dict
    """
    logger.info(f"Processing {task}", extra={{"job_id": ctx.job_id}})
    
    async with get_db_session() as db:
        # TODO: Implement {task} logic
        pass
    
    metrics.increment("{task}_completed")
    return {{"status": "done"}}'''
            handlers.append(handler)
            registry_entries.append(f'    "{task}": {task},')
        
        content = templates.TASKS_PY.format(
            name=self.name,
            task_handlers="\n\n\n".join(handlers),
            task_registry="\n".join(registry_entries),
        )
        self._write("tasks.py", content)
    
    def _generate_dockerfile(self):
        """Generate Dockerfile."""
        content = templates.DOCKERFILE.format(
            name=self.name,
            port=self.manifest.port,
        )
        self._write("Dockerfile", content)
    
    def _generate_docker_compose(self):
        """Generate docker-compose.yml."""
        m = self.manifest
        
        # Redis service
        redis_env = ""
        redis_service = ""
        depends_on = ""
        if m.redis.enabled:
            redis_env = f"      - {m.redis.url_env}=redis://redis:6379"
            redis_service = '''
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data'''
            depends_on = '''    depends_on:
      - redis'''
        
        # Database service
        db_env = ""
        db_service = ""
        db_name_compose = m.database.name
        
        if m.database.type == "postgres":
            db_env = """      - DATABASE_HOST=postgres
      - DATABASE_PORT=5432
      - DATABASE_USER=postgres
      - DATABASE_PASSWORD=postgres"""
            db_service = '''
  postgres:
    image: postgres:15-alpine
    environment:
      - POSTGRES_DB=app
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=postgres
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data'''
            db_name_compose = "app"
            if depends_on:
                depends_on = depends_on.replace("redis", "redis\n      - postgres")
            else:
                depends_on = '''    depends_on:
      - postgres'''
        
        content = templates.DOCKER_COMPOSE.format(
            name=self.name,
            port=m.port,
            jwt_secret_env=m.auth.jwt_secret_env,
            db_type=m.database.type,
            db_name_compose=db_name_compose,
            redis_env=redis_env,
            db_env=db_env,
            depends_on=depends_on,
            redis_service=redis_service,
            db_service=db_service,
        )
        
        # Add volumes if needed
        if m.redis.enabled or m.database.type == "postgres":
            content += "\nvolumes:"
            if m.redis.enabled:
                content += "\n  redis_data:"
            if m.database.type == "postgres":
                content += "\n  postgres_data:"
        
        self._write("docker-compose.yml", content)
    
    def _generate_requirements(self):
        """Generate requirements.txt."""
        m = self.manifest
        
        # Database requirements
        db_reqs = {
            "sqlite": "# SQLite is built-in",
            "postgres": "asyncpg>=0.29.0\npsycopg2-binary>=2.9.9",
            "mysql": "aiomysql>=0.2.0\nmysqlclient>=2.2.0",
        }
        db_requirements = db_reqs.get(m.database.type, "")
        
        # Redis requirements
        redis_requirements = "redis>=5.0.0" if m.redis.enabled else "# Redis not enabled"
        
        content = templates.REQUIREMENTS_TXT.format(
            name=self.name,
            db_requirements=db_requirements,
            redis_requirements=redis_requirements,
        )
        self._write("requirements.txt", content)
    
    def _generate_env_example(self):
        """Generate .env.example."""
        m = self.manifest
        
        # Database env vars
        db_env = []
        if m.database.type != "sqlite":
            db_env.append(f"{self.name_upper}_DATABASE_HOST={m.database.host}")
            port = m.database.port or {"postgres": 5432, "mysql": 3306}.get(m.database.type)
            db_env.append(f"{self.name_upper}_DATABASE_PORT={port}")
            db_env.append(f"{self.name_upper}_DATABASE_USER={m.database.user or 'user'}")
            db_env.append(f"{self.name_upper}_{m.database.password_env or 'DATABASE_PASSWORD'}=secret")
        db_env_vars = "\n".join(db_env)
        
        # Redis env vars
        redis_env = f"{self.name_upper}_{m.redis.url_env}=redis://localhost:6379" if m.redis.enabled else f"# {self.name_upper}_{m.redis.url_env}="
        
        content = templates.ENV_EXAMPLE.format(
            name=self.name,
            name_upper=self.name_upper,
            host=m.host,
            port=m.port,
            db_type=m.database.type,
            db_name=m.database.name,
            db_env_vars=db_env_vars,
            jwt_secret_env=m.auth.jwt_secret_env,
            auth_enabled=str(m.auth.enabled).lower(),
            allow_signup=str(m.auth.allow_signup).lower(),
            redis_env_vars=redis_env,
            cors_origins=",".join(m.cors.origins),
        )
        self._write(".env.example", content)
    
    def _generate_manifest(self):
        """Generate app.manifest.yaml."""
        m = self.manifest
        
        # CORS origins as YAML list
        cors_yaml = "\n".join(f"    - {origin}" for origin in m.cors.origins)
        
        # Tasks as YAML list
        tasks_yaml = "\n".join(f"  - {task}" for task in m.tasks) if m.tasks else "  # No tasks defined"
        
        # Entities as YAML
        entities_yaml_parts = []
        for entity in m.entities:
            fields_yaml = "\n".join(f"      - name: {f.name}\n        type: {f.type}" for f in entity.fields)
            entities_yaml_parts.append(f"""  - name: {entity.name}
    fields:
{fields_yaml}
    workspace_scoped: {str(entity.workspace_scoped).lower()}
    generate_routes: {str(entity.generate_routes).lower()}""")
        
        entities_yaml = "\n".join(entities_yaml_parts) if entities_yaml_parts else "  # No entities defined"
        
        content = templates.MANIFEST_YAML.format(
            name=self.name,
            version=m.version,
            description=m.description or "",
            db_type=m.database.type,
            db_name=m.database.name,
            redis_enabled=str(m.redis.enabled).lower(),
            auth_enabled=str(m.auth.enabled).lower(),
            allow_signup=str(m.auth.allow_signup).lower(),
            cors_origins_yaml=cors_yaml,
            tasks_yaml=tasks_yaml,
            entities_yaml=entities_yaml,
        )
        self._write("app.manifest.yaml", content)


# =============================================================================
# CLI
# =============================================================================

def cmd_new(args):
    """Handle 'new' command."""
    name = args.name
    
    # Load from manifest or build from args
    if args.from_manifest:
        manifest = AppManifest.from_yaml(args.from_manifest)
        # Override name if provided and different
        if name != manifest.name:
            # Recreate with new name but keep all other config objects
            manifest = AppManifest(
                name=name,
                version=manifest.version,
                description=manifest.description,
                database=manifest.database,
                redis=manifest.redis,
                auth=manifest.auth,
                cors=manifest.cors,
                tasks=manifest.tasks,
                entities=manifest.entities,
                api_prefix=manifest.api_prefix,
                host=manifest.host,
                port=manifest.port,
                debug_env=manifest.debug_env,
            )
    else:
        # Build manifest from CLI args
        db_config = DatabaseConfig(
            type=args.db or "sqlite",
            name=args.db_name or f"./data/{name}.db",
        )
        
        redis_config = RedisConfig(enabled=args.redis)
        
        auth_config = AuthConfig(
            enabled=not args.no_auth,
            allow_signup=args.allow_signup,
        )
        
        # Parse tasks
        tasks = []
        if args.tasks:
            tasks = [t.strip() for t in args.tasks.split(",")]
        
        # Parse entities
        entities = []
        if args.entities:
            for entity_def in args.entities.split(","):
                entity_def = entity_def.strip()
                # Format: "entity_name" or "entity_name:field1,field2"
                if ":" in entity_def:
                    entity_name, fields_str = entity_def.split(":", 1)
                    fields = [EntityField(name=f.strip()) for f in fields_str.split(";")]
                else:
                    entity_name = entity_def
                    fields = []
                entities.append(EntityConfig(name=entity_name, fields=fields))
        
        manifest = AppManifest(
            name=name,
            version=args.version or "1.0.0",
            description=args.description or "",
            database=db_config,
            redis=redis_config,
            auth=auth_config,
            tasks=tasks,
            entities=entities,
        )
    
    # Determine output directory
    if args.output:
        output_dir = Path(args.output)
    else:
        # Default: current working directory / app name
        output_dir = Path.cwd() / name
    
    # Generate scaffold
    generator = ScaffoldGenerator(manifest, output_dir)
    generator.generate()


def cmd_manifest(args):
    """Handle 'manifest' command - generate manifest only."""
    name = args.name
    
    manifest = AppManifest(
        name=name,
        version=args.version or "1.0.0",
        description=args.description or f"{name} service",
    )
    
    output_path = args.output or f"{name}.manifest.yaml"
    manifest.to_yaml(output_path)
    print(f"✅ Generated {output_path}")
    print(f"\nEdit the manifest then run:")
    print(f"   appctl new {name} --from-manifest {output_path}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Application scaffold generator for app_kernel services",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  appctl new myapp                          # Creates ./myapp/
  appctl new myapp --db postgres --redis    # With Postgres and Redis
  appctl new myapp --tasks job1,job2        # With background tasks
  appctl new myapp --entities widget:name;color  # With entity and fields
  appctl new myapp --from-manifest app.yaml # From manifest file
  appctl new myapp --output /other/path     # Custom output location
  appctl manifest myapp                     # Generate manifest only

Windows (from services/ folder):
  Drag myapp.manifest.yaml onto new_app.bat
        """,
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # 'new' command
    new_parser = subparsers.add_parser("new", help="Create new app scaffold")
    new_parser.add_argument("name", help="App name")
    new_parser.add_argument("--from-manifest", "-m", help="Load from manifest file")
    new_parser.add_argument("--output", "-o", help="Output directory (default: services/<name>)")
    new_parser.add_argument("--version", "-v", default="1.0.0", help="App version")
    new_parser.add_argument("--description", "-d", help="App description")
    new_parser.add_argument("--db", choices=["sqlite", "postgres", "mysql"], default="sqlite", help="Database type")
    new_parser.add_argument("--db-name", help="Database name/path")
    new_parser.add_argument("--redis", action="store_true", help="Enable Redis")
    new_parser.add_argument("--no-auth", action="store_true", help="Disable authentication")
    new_parser.add_argument("--allow-signup", action="store_true", help="Allow self-signup")
    new_parser.add_argument("--tasks", help="Comma-separated task names")
    new_parser.add_argument("--entities", help="Entity definitions (name:field1;field2,name2:...)")
    new_parser.set_defaults(func=cmd_new)
    
    # 'manifest' command
    manifest_parser = subparsers.add_parser("manifest", help="Generate manifest file only")
    manifest_parser.add_argument("name", help="App name")
    manifest_parser.add_argument("--output", "-o", help="Output file path")
    manifest_parser.add_argument("--version", "-v", default="1.0.0", help="App version")
    manifest_parser.add_argument("--description", "-d", help="App description")
    manifest_parser.set_defaults(func=cmd_manifest)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    args.func(args)


if __name__ == "__main__":
    main()
