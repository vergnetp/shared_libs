"""
Action Replay - Capture frontend user actions for bug diagnosis.

Frontend auto-POSTs a circular buffer of recent actions when an error occurs.
Backend stores + provides admin UI for diagnosis.

Writes directly to DB (no Redis queue — replays are infrequent, low volume).

Usage:
    # Auto-mounted by bootstrap when action_replay_enabled=True:
    #   POST /api/v1/action-replay         - Save replay (auth optional)
    #   GET  /api/v1/action-replays         - List replays (admin)
    #   GET  /api/v1/action-replays/{id}    - Get full replay (admin)
    #   PATCH /api/v1/action-replays/{id}/resolve - Mark resolved (admin)
    
    # Frontend: import { actionLog } from '@myorg/ui'
    #   actionLog.configure({ saveUrl: '/api/v1/action-replay' })
"""

from .router import create_action_replay_router
from .stores import save_replay, list_replays, get_replay, resolve_replay

__all__ = [
    "create_action_replay_router",
    "save_replay",
    "list_replays",
    "get_replay",
    "resolve_replay",
]
