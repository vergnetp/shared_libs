"""Exceptions for AI agents."""


class AgentError(Exception):
    """Base exception for agent errors."""
    pass


class ProviderError(AgentError):
    """Error from LLM provider."""
    def __init__(self, provider: str, message: str, status_code: int = None):
        self.provider = provider
        self.status_code = status_code
        super().__init__(f"{provider}: {message}")


class ProviderRateLimitError(ProviderError):
    """Rate limit hit."""
    def __init__(self, provider: str, retry_after: float = None):
        self.retry_after = retry_after
        super().__init__(provider, "Rate limit exceeded", 429)


class ProviderAuthError(ProviderError):
    """Authentication failed."""
    def __init__(self, provider: str):
        super().__init__(provider, "Authentication failed", 401)


class ProviderUnavailableError(ProviderError):
    """Provider temporarily unavailable."""
    def __init__(self, provider: str):
        super().__init__(provider, "Service unavailable", 503)


class ContextTooLongError(AgentError):
    """Context exceeds model's max tokens."""
    def __init__(self, tokens: int, max_tokens: int):
        self.tokens = tokens
        self.max_tokens = max_tokens
        super().__init__(f"Context {tokens} tokens exceeds max {max_tokens}")


class ToolError(AgentError):
    """Error executing a tool."""
    def __init__(self, tool_name: str, message: str):
        self.tool_name = tool_name
        super().__init__(f"Tool '{tool_name}': {message}")


class ToolNotFoundError(ToolError):
    """Tool not registered."""
    def __init__(self, tool_name: str):
        super().__init__(tool_name, "not found")


class GuardrailError(AgentError):
    """Content blocked by guardrail."""
    def __init__(self, guardrail: str, reason: str):
        self.guardrail = guardrail
        self.reason = reason
        super().__init__(f"Blocked by {guardrail}: {reason}")


class ThreadNotFoundError(AgentError):
    """Thread not found."""
    def __init__(self, thread_id: str):
        self.thread_id = thread_id
        super().__init__(f"Thread not found: {thread_id}")


class AgentNotFoundError(AgentError):
    """Agent not found."""
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        super().__init__(f"Agent not found: {agent_id}")
