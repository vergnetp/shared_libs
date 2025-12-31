"""
Generated routes - AUTO-GENERATED from manifest.yaml
DO NOT EDIT - changes will be overwritten on regenerate
"""

from fastapi import APIRouter

from .item import router as item_router

# Combined router for all generated CRUD endpoints
router = APIRouter()

router.include_router(item_router)