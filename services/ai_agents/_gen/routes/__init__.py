"""
Generated routes - AUTO-GENERATED from manifest.yaml
DO NOT EDIT - changes will be overwritten on regenerate
"""

from fastapi import APIRouter

from .workspace import router as workspace_router
from .workspace_member import router as workspace_member_router
from .agent import router as agent_router
from .thread import router as thread_router
from .message import router as message_router
from .document import router as document_router
from .document_chunk import router as document_chunk_router
from .user_context import router as user_context_router
from .analytics_daily import router as analytics_daily_router

# Combined router for all generated CRUD endpoints
router = APIRouter()

router.include_router(workspace_router)
router.include_router(workspace_member_router)
router.include_router(agent_router)
router.include_router(thread_router)
router.include_router(message_router)
router.include_router(document_router)
router.include_router(document_chunk_router)
router.include_router(user_context_router)
router.include_router(analytics_daily_router)