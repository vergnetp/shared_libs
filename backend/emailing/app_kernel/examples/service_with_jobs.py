"""
Example: Service with Background Jobs

Shows how to add background task processing.

Requires Redis:
    JWT_SECRET=my-secret REDIS_URL=redis://localhost:6379 uvicorn example_with_jobs:app --reload

Run worker in separate terminal:
    python -c "from example_with_jobs import run_workers; run_workers()"
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, Any

from app_kernel.bootstrap import create_service, ServiceConfig
from app_kernel import get_current_user, get_job_client, JobContext

# =============================================================================
# Task Handlers (your background work)
# =============================================================================

async def process_order(payload: Dict[str, Any], ctx: JobContext) -> Dict[str, Any]:
    """
    Process an order in the background.
    
    This runs in a worker process, not the API process.
    """
    order_id = payload["order_id"]
    items = payload.get("items", [])
    
    # Simulate processing
    import asyncio
    await asyncio.sleep(2)
    
    # Log progress
    from app_kernel import get_logger
    logger = get_logger()
    logger.info(f"Processed order {order_id} with {len(items)} items")
    
    return {"status": "completed", "order_id": order_id}


async def send_notification(payload: Dict[str, Any], ctx: JobContext) -> Dict[str, Any]:
    """Send notification to user."""
    user_id = payload["user_id"]
    message = payload["message"]
    
    # In real app: send email, push notification, etc.
    from app_kernel import get_logger
    logger = get_logger()
    logger.info(f"Notification to {user_id}: {message}")
    
    return {"sent": True}


# =============================================================================
# Routes
# =============================================================================

router = APIRouter(prefix="/orders", tags=["orders"])

class OrderCreate(BaseModel):
    items: list[str]

class OrderResponse(BaseModel):
    order_id: str
    job_id: str
    status: str

@router.post("", response_model=OrderResponse, status_code=202)
async def create_order(data: OrderCreate, user=Depends(get_current_user)):
    """Create order and queue for processing."""
    import uuid
    order_id = str(uuid.uuid4())
    
    # Enqueue background job
    client = get_job_client()
    result = await client.enqueue(
        task_name="process_order",
        payload={"order_id": order_id, "items": data.items},
        user_id=user.id,
    )
    
    # Also send notification
    await client.enqueue(
        task_name="send_notification",
        payload={
            "user_id": user.id,
            "message": f"Order {order_id} received!",
        },
        user_id=user.id,
    )
    
    return {
        "order_id": order_id,
        "job_id": result.job_id,
        "status": "queued",
    }

# =============================================================================
# App Creation
# =============================================================================

app = create_service(
    name="order_service",
    version="1.0.0",
    routers=[router],
    
    # Register task handlers
    tasks={
        "process_order": process_order,
        "send_notification": send_notification,
    },
    
    config=ServiceConfig.from_env(),
)


# =============================================================================
# Worker Runner (for separate process)
# =============================================================================

def run_workers():
    """Run workers in this process."""
    import asyncio
    from app_kernel import run_worker
    
    # This blocks and processes jobs
    asyncio.run(run_worker())


if __name__ == "__main__":
    # When run directly, start workers
    run_workers()
