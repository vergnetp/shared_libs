"""
Multi-agent orchestration patterns.

Provides:
- ParallelAgents: Run multiple agents concurrently
- Supervisor: Coordinate workers with a planning agent
- Pipeline: Sequential agent chain
- Debate: Agents discuss and reach consensus
"""

from .parallel import ParallelAgents, parallel_chat
from .supervisor import Supervisor, SupervisorConfig
from .pipeline import Pipeline
from .debate import Debate

__all__ = [
    "ParallelAgents",
    "parallel_chat",
    "Supervisor",
    "SupervisorConfig",
    "Pipeline",
    "Debate",
]
