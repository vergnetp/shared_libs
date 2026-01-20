"""Context system for user context management."""

from .base import ContextProvider, ContextBuilder
from .default import (
    DefaultContextProvider, 
    InMemoryContextProvider,
    DefaultContextBuilder,
    deep_merge,
)

__all__ = [
    "ContextProvider",
    "ContextBuilder",
    "DefaultContextProvider",
    "InMemoryContextProvider",
    "DefaultContextBuilder",
    "deep_merge",
]
