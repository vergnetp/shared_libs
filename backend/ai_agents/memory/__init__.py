"""Memory strategies for conversation context."""

from .base import MemoryStrategy
from .last_n import LastNMemory
from .first_last import FirstLastMemory
from .summarize import SummarizeMemory
from .token_window import TokenWindowMemory


def get_memory_strategy(name: str, **kwargs) -> MemoryStrategy:
    """
    Get memory strategy by name.
    
    Args:
        name: Strategy name (last_n, first_last, summarize, token_window)
        **kwargs: Strategy-specific params
    """
    strategies = {
        "last_n": LastNMemory,
        "first_last": FirstLastMemory,
        "summarize": SummarizeMemory,
        "token_window": TokenWindowMemory,
    }
    
    if name not in strategies:
        raise ValueError(f"Unknown memory strategy: {name}")
    
    return strategies[name](**kwargs)


__all__ = [
    "MemoryStrategy",
    "LastNMemory",
    "FirstLastMemory",
    "SummarizeMemory",
    "TokenWindowMemory",
    "get_memory_strategy",
]
