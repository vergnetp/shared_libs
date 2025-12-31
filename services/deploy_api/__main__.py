"""
Run deploy_api with: python -m services.deploy_api
"""

import uvicorn
from .config import get_settings

settings = get_settings()

if __name__ == "__main__":
    uvicorn.run(
        "services.deploy_api.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
