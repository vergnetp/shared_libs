"""
LLM cost tracking and premium model detection.

Stub implementation - real costs would be defined based on provider pricing.
"""

# Premium models that should be used sparingly
PREMIUM_MODELS = {
    "gpt-4",
    "gpt-4-turbo",
    "gpt-4o",
    "claude-opus-4-20250514",
    "claude-3-opus-20240229",
}

# Cost per 1M tokens (input/output)
MODEL_COSTS = {
    # OpenAI
    "gpt-4o": {"input": 5.0, "output": 15.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.6},
    "gpt-4-turbo": {"input": 10.0, "output": 30.0},
    "gpt-4": {"input": 30.0, "output": 60.0},
    "gpt-3.5-turbo": {"input": 0.5, "output": 1.5},
    # Anthropic
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
    "claude-opus-4-20250514": {"input": 15.0, "output": 75.0},
    "claude-3-5-sonnet-20241022": {"input": 3.0, "output": 15.0},
    "claude-haiku-3-20240307": {"input": 0.25, "output": 1.25},
    # Groq (very cheap)
    "llama-3.3-70b-versatile": {"input": 0.59, "output": 0.79},
    "llama-3.1-8b-instant": {"input": 0.05, "output": 0.08},
    "mixtral-8x7b-32768": {"input": 0.24, "output": 0.24},
}


def is_premium_model(model: str) -> bool:
    """Check if a model is premium (expensive)."""
    return model in PREMIUM_MODELS


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """
    Calculate cost in USD for a completion.
    
    Args:
        model: Model name
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        
    Returns:
        Cost in USD
    """
    costs = MODEL_COSTS.get(model, {"input": 1.0, "output": 2.0})
    
    input_cost = (input_tokens / 1_000_000) * costs["input"]
    output_cost = (output_tokens / 1_000_000) * costs["output"]
    
    return input_cost + output_cost


def get_model_costs(model: str) -> dict:
    """Get cost rates for a model."""
    return MODEL_COSTS.get(model, {"input": 1.0, "output": 2.0})
