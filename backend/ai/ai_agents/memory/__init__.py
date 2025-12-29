"""Memory strategies for conversation context."""

from .base import MemoryStrategy
from .last_n import LastNMemory
from .first_last import FirstLastMemory
from .summarize import SummarizeMemory, SummarizationHelper
from .token_window import TokenWindowMemory, estimate_tokens
from .vector import VectorMemory


def get_memory_strategy(name: str, **kwargs) -> MemoryStrategy:
    """
    Get memory strategy by name.
    
    Args:
        name: Strategy name (last_n, first_last, summarize, token_window, vector)
        **kwargs: Strategy-specific params
    """
    strategies = {
        "last_n": LastNMemory,
        "first_last": FirstLastMemory,
        "summarize": SummarizeMemory,
        "token_window": TokenWindowMemory,
        "vector": VectorMemory,
    }
    
    if name not in strategies:
        raise ValueError(f"Unknown memory strategy: {name}")
    
    return strategies[name](**kwargs)


__all__ = [
    "MemoryStrategy",
    "LastNMemory",
    "FirstLastMemory",
    "SummarizeMemory",
    "SummarizationHelper",
    "TokenWindowMemory",
    "VectorMemory",
    "get_memory_strategy",
    "estimate_tokens",
]
