"""Model configuration loader.

Loads model info from models.json for costs, context limits, and fallbacks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any


# Load models.json from same directory
_MODELS_JSON_PATH = Path(__file__).parent / "models.json"
_models_cache: Optional[Dict[str, Any]] = None


def _load_models() -> Dict[str, Any]:
    """Load models.json, cached."""
    global _models_cache
    if _models_cache is None:
        with open(_MODELS_JSON_PATH) as f:
            _models_cache = json.load(f)
    return _models_cache


def reload_models() -> None:
    """Force reload of models.json (useful after manual edits)."""
    global _models_cache
    _models_cache = None
    _load_models()


@dataclass
class ModelInfo:
    """Model configuration."""
    name: str
    provider: str
    max_context: int
    max_output: int
    input_cost_per_million: float
    output_cost_per_million: float
    tier: int
    fallback_to: Optional[str]
    type: str = "chat"  # "chat" or "embedding"
    dimensions: Optional[int] = None  # for embeddings
    
    @property
    def is_free(self) -> bool:
        return self.input_cost_per_million == 0 and self.output_cost_per_million == 0
    
    @property
    def is_premium(self) -> bool:
        return self.tier >= 3
    
    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost for a completion."""
        input_cost = (input_tokens / 1_000_000) * self.input_cost_per_million
        output_cost = (output_tokens / 1_000_000) * self.output_cost_per_million
        return input_cost + output_cost
    
    def get_fallback_chain(self) -> list[str]:
        """Get list of models to try, from self to cheapest."""
        chain = [self.name]
        current = self.fallback_to
        seen = {self.name}
        
        while current and current not in seen:
            chain.append(current)
            seen.add(current)
            info = get_model_info(current)
            current = info.fallback_to if info else None
        
        return chain


def get_model_info(model: str) -> Optional[ModelInfo]:
    """Get model configuration."""
    data = _load_models()
    model_data = data.get("models", {}).get(model)
    
    if not model_data:
        return None
    
    return ModelInfo(
        name=model,
        provider=model_data.get("provider", "unknown"),
        max_context=model_data.get("max_context", 8192),
        max_output=model_data.get("max_output", 4096),
        input_cost_per_million=model_data.get("input_cost_per_million", 0.0),
        output_cost_per_million=model_data.get("output_cost_per_million", 0.0),
        tier=model_data.get("tier", 1),
        fallback_to=model_data.get("fallback_to"),
        type=model_data.get("type", "chat"),
        dimensions=model_data.get("dimensions"),
    )


def get_default_model(provider: str) -> Optional[str]:
    """Get default model for a provider."""
    data = _load_models()
    return data.get("provider_defaults", {}).get(provider)


def list_models(provider: str = None, type: str = None) -> list[ModelInfo]:
    """List all models, optionally filtered."""
    data = _load_models()
    models = []
    
    for name, model_data in data.get("models", {}).items():
        if provider and model_data.get("provider") != provider:
            continue
        if type and model_data.get("type", "chat") != type:
            continue
        
        info = get_model_info(name)
        if info:
            models.append(info)
    
    return models


def get_max_context(model: str) -> int:
    """Get max context tokens for a model."""
    info = get_model_info(model)
    return info.max_context if info else 8192


def get_max_output(model: str) -> int:
    """Get max output tokens for a model."""
    info = get_model_info(model)
    return info.max_output if info else 4096


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost for a completion."""
    info = get_model_info(model)
    if info:
        return info.calculate_cost(input_tokens, output_tokens)
    return 0.0


def get_degraded_model(model: str, budget_percent_used: float) -> str:
    """Get cheaper model based on budget usage."""
    info = get_model_info(model)
    if not info:
        return model
    
    chain = info.get_fallback_chain()
    
    if budget_percent_used < 0.8:
        return chain[0]  # Original
    elif budget_percent_used < 0.95 and len(chain) > 1:
        return chain[1]  # First fallback
    elif len(chain) > 2:
        return chain[-1]  # Cheapest
    
    return chain[-1]


# Backwards compatibility with costs.py
def get_model_tier(model: str) -> int:
    """Get model tier (0=embedding, 1=fast, 2=mid, 3=premium)."""
    info = get_model_info(model)
    return info.tier if info else 1


def is_premium_model(model: str) -> bool:
    """Check if model is premium tier."""
    return get_model_tier(model) >= 3


def get_providers() -> Dict[str, Any]:
    """Get all provider configurations."""
    data = _load_models()
    return data.get("providers", {})


def get_models_by_provider() -> Dict[str, list[str]]:
    """Get models grouped by provider."""
    data = _load_models()
    by_provider = {}
    
    for name, model_data in data.get("models", {}).items():
        # Skip embedding models
        if model_data.get("type") == "embedding":
            continue
            
        provider = model_data.get("provider", "unknown")
        if provider not in by_provider:
            by_provider[provider] = []
        by_provider[provider].append(name)
    
    return by_provider


def get_models_catalog() -> Dict[str, Any]:
    """
    Get full models catalog for UI dropdowns.
    
    Returns:
        {
            "providers": {"openai": {...}, ...},
            "models": {"openai": ["gpt-4o", ...], ...},
            "defaults": {"openai": "gpt-4o-mini", ...},
            "details": {"gpt-4o": {...}, ...}
        }
    """
    data = _load_models()
    
    # Group models by provider (exclude embeddings)
    by_provider = {}
    details = {}
    
    for name, model_data in data.get("models", {}).items():
        if model_data.get("type") == "embedding":
            continue
            
        provider = model_data.get("provider", "unknown")
        if provider not in by_provider:
            by_provider[provider] = []
        by_provider[provider].append(name)
        
        # Add details
        details[name] = {
            "provider": provider,
            "free": model_data.get("input_cost_per_million", 0) == 0,
            "tier": model_data.get("tier", 1),
            "max_context": model_data.get("max_context", 8192),
            "recommended": model_data.get("recommended", False),
            "size_gb": model_data.get("size_gb"),
        }
    
    return {
        "providers": data.get("providers", {}),
        "models": by_provider,
        "defaults": data.get("provider_defaults", {}),
        "details": details,
    }
