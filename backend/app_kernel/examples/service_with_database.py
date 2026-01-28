"""
Example: Widget Service with Database

This shows how to create a complete service with persistent storage.
The database automatically creates tables and columns as needed.

Run with:
    JWT_SECRET=my-secret DATABASE_PATH=./data/widgets.db \
    uvicorn example_service_db:app --reload

What you get:
    - POST /api/v1/widgets - Create widget
    - GET /api/v1/widgets - List widgets  
    - GET /api/v1/widgets/{id} - Get widget
    - DELETE /api/v1/widgets/{id} - Delete widget
    - POST /api/v1/auth/login - Login
    - GET /healthz - Health check
    - GET /metrics - Metrics (admin only)
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from dataclasses import dataclass

from app_kernel import create_service, ServiceConfig, get_current_user
from app_kernel.db import db_connection

# =============================================================================
# Models (optional - for validation and documentation)
# =============================================================================

@dataclass
class Widget:
    """A widget entity - defines the expected fields."""
    name: str
    color: str = "blue"
    owner_id: Optional[str] = None

# Pydantic for API validation
class WidgetCreate(BaseModel):
    name: str
    color: str = "blue"

class WidgetResponse(BaseModel):
    id: str
    name: str
    color: str
    owner_id: str
    created_at: Optional[str] = None

# =============================================================================
# Routes
# =============================================================================

router = APIRouter(prefix="/widgets", tags=["widgets"])


@router.post("", response_model=WidgetResponse, status_code=201)
async def create_widget(
    data: WidgetCreate, 
    user=Depends(get_current_user),
    db=Depends(db_connection),
):
    """Create a new widget."""
    # Database auto-creates 'widgets' table if it doesn't exist
    # Auto-adds any new columns, auto-generates id and timestamps
    widget = await db.save_entity("widgets", {
        "name": data.name,
        "color": data.color,
        "owner_id": user.id,
    })
    return widget


@router.get("", response_model=List[WidgetResponse])
async def list_widgets(
    user=Depends(get_current_user),
    db=Depends(db_connection),
):
    """List widgets. Admins see all, users see their own."""
    if user.role == "admin":
        return await db.find_entities("widgets")
    
    return await db.find_entities(
        "widgets",
        where_clause="[owner_id] = ?",
        params=(user.id,),
    )


@router.get("/{widget_id}", response_model=WidgetResponse)
async def get_widget(
    widget_id: str,
    user=Depends(get_current_user),
    db=Depends(db_connection),
):
    """Get a specific widget."""
    widget = await db.get_entity("widgets", widget_id)
    if not widget:
        raise HTTPException(404, "Widget not found")
    if widget["owner_id"] != user.id and user.role != "admin":
        raise HTTPException(403, "Access denied")
    return widget


@router.delete("/{widget_id}", status_code=204)
async def delete_widget(
    widget_id: str,
    user=Depends(get_current_user),
    db=Depends(db_connection),
):
    """Delete a widget (soft delete)."""
    widget = await db.get_entity("widgets", widget_id)
    if not widget:
        raise HTTPException(404, "Widget not found")
    if widget["owner_id"] != user.id and user.role != "admin":
        raise HTTPException(403, "Access denied")
    
    await db.delete_entity("widgets", widget_id, permanent=False)


# =============================================================================
# App Creation
# =============================================================================

app = create_service(
    name="widget_service",
    version="1.0.0",
    routers=[router],
    config=ServiceConfig.from_env(),
)

# That's it! The database:
# - Auto-creates tables on first save
# - Auto-adds columns for new fields
# - Auto-generates id, created_at, updated_at
# - Tracks history of all changes
# - Supports soft delete
