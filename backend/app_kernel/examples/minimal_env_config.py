"""
Example: Minimal service using ServiceConfig.from_env()

This shows the simplest possible setup - just env vars, no custom config file.

Usage:
    # Set env vars
    export JWT_SECRET=your-secret
    export DATABASE_NAME=./data/app.db
    export SAAS_ENABLED=true
    export EMAIL_ENABLED=true
    export SMTP_HOST=smtp.gmail.com
    export SMTP_USER=noreply@example.com
    export SMTP_PASSWORD=app-password
    
    # Run
    uvicorn minimal_example:app --reload
"""

from fastapi import APIRouter
from .. import create_service, ServiceConfig


# Your business logic routes
router = APIRouter(prefix="/api/v1", tags=["example"])

@router.get("/hello")
async def hello():
    return {"message": "Hello from minimal service!"}


# Create app - kernel handles everything from env vars
app = create_service(
    name="minimal-service",
    routers=[router],
    config=ServiceConfig.from_env(),  # ← All config from env vars!
)


# That's it! You get:
# - Auth (JWT login/register) from JWT_SECRET
# - Database from DATABASE_NAME
# - SaaS (workspaces/invites) from SAAS_ENABLED=true
# - Email (invite emails) from EMAIL_ENABLED=true + SMTP_*
# - Health checks at /healthz, /readyz
# - Metrics at /metrics
# - CORS, rate limiting, etc.
