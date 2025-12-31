"""
Generated code - AUTO-GENERATED from manifest.yaml
DO NOT EDIT - changes will be overwritten on regenerate

For custom logic, put code in src/
"""

from .db_schema import init_schema
from .schemas import *
from .crud import EntityCRUD
from .routes import router as gen_router

__all__ = ["init_schema", "EntityCRUD", "gen_router"]
