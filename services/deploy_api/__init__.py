"""
Deploy API - Deployment Management Service

Run with:
    uvicorn services.deploy_api.main:app --reload
    
Or:
    python -m services.deploy_api
"""

from .main import app, create_app

__all__ = ["app", "create_app"]
