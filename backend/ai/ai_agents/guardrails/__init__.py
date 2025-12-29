"""Safety guardrails."""

from .injection import (
    InjectionGuard,
    EmbeddingInjectionGuard,
    PatternInjectionGuard,
    LLMInjectionGuard,
    check_injection,
    load_attack_examples,
    DEFAULT_ATTACK_EXAMPLES,
    InjectionGuardrail,  # Legacy alias
)
from .content import ContentGuardrail, WordlistGuardrail

__all__ = [
    # Injection detection (layered)
    "InjectionGuard",
    "EmbeddingInjectionGuard",
    "PatternInjectionGuard",
    "LLMInjectionGuard",
    "check_injection",
    # Attack examples
    "load_attack_examples",
    "DEFAULT_ATTACK_EXAMPLES",
    # Legacy
    "InjectionGuardrail",
    # Content filtering
    "ContentGuardrail",
    "WordlistGuardrail",
]
