"""Cost tracking and budgeting for AI agents."""

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


# Model info: pricing per 1M tokens + tier (1=fast/cheap, 2=mid, 3=premium)
MODEL_INFO = {
    # OpenAI
    "gpt-4o-mini": {"input": 0.15, "output": 0.60, "tier": 1},
    "gpt-4o": {"input": 2.50, "output": 10.00, "tier": 2},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00, "tier": 2},
    "gpt-5": {"input": 1.25, "output": 10.00, "tier": 3},
    "gpt-5.1": {"input": 1.25, "output": 10.00, "tier": 3},
    "gpt-5.2": {"input": 1.25, "output": 10.00, "tier": 3},
    # Anthropic
    "claude-haiku-3-20250307": {"input": 0.25, "output": 1.25, "tier": 1},
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00, "tier": 3},
    "claude-opus-4-20250514": {"input": 15.00, "output": 75.00, "tier": 3},
    # Groq (free tier)
    "llama-3.3-70b-versatile": {"input": 0.0, "output": 0.0, "tier": 1},
    "llama-3.1-8b-instant": {"input": 0.0, "output": 0.0, "tier": 1},
    "mixtral-8x7b-32768": {"input": 0.0, "output": 0.0, "tier": 1},
    "gemma2-9b-it": {"input": 0.0, "output": 0.0, "tier": 1},
    # Ollama (free, local)
    "llama3.1": {"input": 0.0, "output": 0.0, "tier": 1},
    "llama3.2": {"input": 0.0, "output": 0.0, "tier": 1},
    "qwen3:8b": {"input": 0.0, "output": 0.0, "tier": 1},
    "mistral": {"input": 0.0, "output": 0.0, "tier": 1},
    # Embeddings (input only, no output)
    "text-embedding-3-small": {"input": 0.02, "output": 0.0, "tier": 0},
    "text-embedding-3-large": {"input": 0.13, "output": 0.0, "tier": 0},
    "text-embedding-ada-002": {"input": 0.10, "output": 0.0, "tier": 0},
}

# Backwards compatibility
PROVIDER_COSTS = {k: {"input": v["input"], "output": v["output"]} for k, v in MODEL_INFO.items()}


def get_model_tier(model: str) -> int:
    """Get model tier (1=fast, 2=mid, 3=premium)."""
    return MODEL_INFO.get(model, {}).get("tier", 1)


def is_premium_model(model: str) -> bool:
    """Check if model is premium tier."""
    return get_model_tier(model) >= 3

# Model degradation tiers (fallback to cheaper models)
MODEL_TIERS = {
    "gpt-4o": ["gpt-4o", "gpt-4o-mini"],
    "gpt-4o-mini": ["gpt-4o-mini"],
    "gpt-4-turbo": ["gpt-4-turbo", "gpt-4o", "gpt-4o-mini"],
    "claude-sonnet-4-20250514": ["claude-sonnet-4-20250514", "claude-haiku-3-20250307"],
    "claude-opus-4-20250514": ["claude-opus-4-20250514", "claude-sonnet-4-20250514", "claude-haiku-3-20250307"],
}


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost for a completion.
    
    For cascading models ("fast+premium"), this is a fallback - the cascading
    provider normally pre-calculates and passes cost directly to add_usage().
    """
    # Handle cascading models (fast+premium) as fallback
    if "+" in model:
        models = model.split("+")
        total_cost = 0.0
        # Split tokens roughly equally for estimation
        for m in models:
            pricing = PROVIDER_COSTS.get(m, {"input": 0.0, "output": 0.0})
            total_cost += (input_tokens / len(models) / 1_000_000) * pricing["input"]
            total_cost += (output_tokens / len(models) / 1_000_000) * pricing["output"]
        return total_cost
    
    pricing = PROVIDER_COSTS.get(model, {"input": 0.0, "output": 0.0})
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return input_cost + output_cost


def get_degraded_model(model: str, budget_percent_used: float) -> str:
    """Get cheaper model based on budget usage."""
    tiers = MODEL_TIERS.get(model, [model])
    
    if budget_percent_used < 0.8:
        return tiers[0]  # Original model
    elif budget_percent_used < 0.95 and len(tiers) > 1:
        return tiers[1]  # Mid-tier
    elif len(tiers) > 2:
        return tiers[-1]  # Cheapest
    return tiers[-1]


@dataclass
class CostTracker:
    """Track costs per conversation and total."""
    
    conversation_cost: float = 0.0
    total_cost: float = 0.0
    conversation_tokens: dict = field(default_factory=lambda: {"input": 0, "output": 0})
    total_tokens: dict = field(default_factory=lambda: {"input": 0, "output": 0})
    
    # Budget
    max_conversation_cost: Optional[float] = None
    max_total_cost: Optional[float] = None
    
    # Tracking
    request_count: int = 0
    conversation_start: datetime = field(default_factory=datetime.now)
    
    def add_usage(self, model: str, input_tokens: int, output_tokens: int, cost: float = None) -> float:
        """Add usage and return cost for this request.
        
        Args:
            model: Model name (or "fast+premium" for cascading)
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            cost: Pre-calculated cost (used by cascading provider)
        """
        if cost is None:
            cost = calculate_cost(model, input_tokens, output_tokens)
        
        self.conversation_cost += cost
        self.total_cost += cost
        
        self.conversation_tokens["input"] += input_tokens
        self.conversation_tokens["output"] += output_tokens
        self.total_tokens["input"] += input_tokens
        self.total_tokens["output"] += output_tokens
        
        self.request_count += 1
        
        return cost
    
    def reset_conversation(self):
        """Reset conversation-level tracking."""
        self.conversation_cost = 0.0
        self.conversation_tokens = {"input": 0, "output": 0}
        self.conversation_start = datetime.now()
    
    @property
    def budget_percent_used(self) -> float:
        """Percentage of conversation budget used."""
        if not self.max_conversation_cost:
            return 0.0
        return self.conversation_cost / self.max_conversation_cost
    
    @property
    def is_over_budget(self) -> bool:
        """Check if over budget."""
        if self.max_conversation_cost and self.conversation_cost >= self.max_conversation_cost:
            return True
        if self.max_total_cost and self.total_cost >= self.max_total_cost:
            return True
        return False
    
    def check_budget(self) -> None:
        """Raise error if over budget."""
        if self.is_over_budget:
            raise BudgetExceededError(
                f"Budget exceeded: conversation=${self.conversation_cost:.4f}, "
                f"total=${self.total_cost:.4f}"
            )
    
    def to_dict(self) -> dict:
        """Export as dict."""
        return {
            "conversation_cost": self.conversation_cost,
            "total_cost": self.total_cost,
            "conversation_tokens": self.conversation_tokens,
            "total_tokens": self.total_tokens,
            "request_count": self.request_count,
            "budget_percent_used": self.budget_percent_used,
        }


class BudgetExceededError(Exception):
    """Raised when cost budget is exceeded."""
    pass