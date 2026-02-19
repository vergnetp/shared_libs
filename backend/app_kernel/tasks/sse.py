"""
Server-Sent Events (SSE) formatting helpers.

All functions return formatted SSE strings ready to yield from
a StreamingResponse async generator.
"""

import json


def sse_event(event: str, data: dict) -> str:
    """Format a generic SSE event.
    
    Includes event name as 'type' in the JSON payload so clients that only
    parse 'data:' lines (and ignore 'event:' lines) still get the event type.
    """
    payload = {"type": event, **data}
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def sse_task_id(task_id: str, **extra) -> str:
    """Emit task_id event so client can call cancel."""
    data = {"task_id": task_id, **extra}
    print(f"[SSE] TASK_ID: {task_id}")
    return sse_event("task_id", data)


def sse_log(message: str, level: str = "info") -> str:
    """Emit a log event."""
    print(f"[SSE] {level.upper()}: {message}")
    return sse_event("log", {"message": message, "level": level})


def sse_complete(success: bool, task_id: str = '', error: str = None, **extra) -> str:
    """Emit completion event."""
    data = {"success": success, "task_id": task_id, "error": error, **extra}
    print(f"[SSE] COMPLETE: success={success}, id={task_id}, error={error}")
    return sse_event("complete", data)


def sse_urls(endpoints: list, domain: str = None) -> str:
    """Emit deployment/service URLs."""
    data = {"endpoints": endpoints}
    if domain:
        data["domain"] = domain
        data["url"] = f"https://{domain}"
    print(f"[SSE] URLS: {data}")
    return sse_event("urls", data)