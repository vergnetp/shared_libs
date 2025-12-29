"""
Example: Minimal Widget Service

This shows how to create a complete service in ~50 lines.

Run with:
    JWT_SECRET=my-secret uvicorn example_service:app --reload

What you get:
    - POST /api/v1/widgets - Create widget
    - GET /api/v1/widgets - List widgets  
    - GET /api/v1/widgets/{id} - Get widget
    - POST /api/v1/auth/login - Login
    - POST /api/v1/auth/register - Register (if enabled)
    - GET /healthz - Health check
    - GET /readyz - Readiness check
    - GET /metrics - Metrics (admin only)
    - GET /docs - OpenAPI docs
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from app_kernel.bootstrap import create_service, ServiceConfig
from app_kernel import get_current_user

# =============================================================================
# Schemas (your business models)
# =============================================================================

class WidgetCreate(BaseModel):
    name: str
    color: str = "blue"

class WidgetResponse(BaseModel):
    id: str
    name: str
    color: str
    owner_id: str

# =============================================================================
# Routes (your business logic)
# =============================================================================

router = APIRouter(prefix="/widgets", tags=["widgets"])

# In-memory store for demo
_widgets = {}

@router.post("", response_model=WidgetResponse, status_code=201)
async def create_widget(data: WidgetCreate, user=Depends(get_current_user)):
    import uuid
    widget_id = str(uuid.uuid4())
    widget = {
        "id": widget_id,
        "name": data.name,
        "color": data.color,
        "owner_id": user.id,
    }
    _widgets[widget_id] = widget
    return widget

@router.get("", response_model=List[WidgetResponse])
async def list_widgets(user=Depends(get_current_user)):
    # Only return user's widgets (unless admin)
    if user.role == "admin":
        return list(_widgets.values())
    return [w for w in _widgets.values() if w["owner_id"] == user.id]

@router.get("/{widget_id}", response_model=WidgetResponse)
async def get_widget(widget_id: str, user=Depends(get_current_user)):
    widget = _widgets.get(widget_id)
    if not widget:
        raise HTTPException(404, "Widget not found")
    if widget["owner_id"] != user.id and user.role != "admin":
        raise HTTPException(403, "Access denied")
    return widget

# =============================================================================
# App Creation (ONE LINE!)
# =============================================================================

app = create_service(
    name="widget_service",
    version="1.0.0",
    routers=[router],
    config=ServiceConfig.from_env(),
)

# That's it! You now have:
# - Auth (JWT tokens)
# - CORS
# - Request logging
# - Metrics
# - Health checks
# - Rate limiting (if REDIS_URL set)
# - Background jobs (if REDIS_URL set)
