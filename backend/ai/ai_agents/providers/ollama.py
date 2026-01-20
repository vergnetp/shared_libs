from __future__ import annotations
"""Ollama provider for local models.

Uses cloud.llm.AsyncOllamaClient for HTTP operations.
No SDK required - Ollama has a simple HTTP API.
"""

from typing import AsyncIterator

# Backend imports
from ....resilience import circuit_breaker, with_timeout
from ....log import info, error

# Local imports
from ..core import ProviderResponse, ProviderError
from .base import LLMProvider
from .utils import build_response


# =============================================================================
# Lazy imports for cloud.llm
# =============================================================================

_ollama_client_class = None
_llm_errors = None


def _get_ollama_client():
    """Lazy import for AsyncOllamaClient."""
    global _ollama_client_class
    if _ollama_client_class is None:
        from ....cloud.llm import AsyncOllamaClient
        _ollama_client_class = AsyncOllamaClient
    return _ollama_client_class


def _get_llm_errors():
    """Lazy import for LLM error classes."""
    global _llm_errors
    if _llm_errors is None:
        from ....cloud.llm import LLMError, LLMConnectionError, LLMTimeoutError
        _llm_errors = {
            "LLMError": LLMError,
            "LLMConnectionError": LLMConnectionError,
            "LLMTimeoutError": LLMTimeoutError,
        }
    return _llm_errors


class OllamaProvider(LLMProvider):
    """Ollama provider for local models.
    
    Uses cloud.llm.AsyncOllamaClient for all HTTP operations.
    Inherits resilience (retry, circuit breaker) from the client.
    """
    
    name = "ollama"
    
    def __init__(self, model: str = "llama3.1", base_url: str = "http://localhost:11434", api_key: str = None, **kwargs):
        """Initialize Ollama provider.
        
        Args:
            model: Model name (e.g., "llama3.2", "mistral", "qwen2.5:3b")
            base_url: Ollama server URL (default: localhost:11434)
            api_key: Ignored - Ollama doesn't need authentication
            **kwargs: Ignored extra arguments for compatibility
        """
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = None
    
    def _get_client(self):
        """Get or create AsyncOllamaClient."""
        if self._client is None:
            AsyncOllamaClient = _get_ollama_client()
            self._client = AsyncOllamaClient(
                model=self.model,
                base_url=self.base_url,
                timeout=300.0,  # Local models can be slow
            )
        return self._client
    
    def _parse_tool_calls(self, response) -> list[dict] | None:
        """Extract tool calls from ChatResponse format."""
        if not response.tool_calls:
            return None
        
        # Convert cloud.llm ToolCall objects to ai_agents format
        tool_calls = []
        for tc in response.tool_calls:
            tool_calls.append({
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": tc.arguments if isinstance(tc.arguments, str) else __import__("json").dumps(tc.arguments),
                }
            })
        return tool_calls if tool_calls else None
    
    @circuit_breaker(name="ollama")
    @with_timeout(300)
    async def run(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] = None,
        **kwargs,
    ) -> ProviderResponse:
        """Run completion using AsyncOllamaClient."""
        info("Calling Ollama", model=self.model, message_count=len(messages))
        
        # Add tool format instructions when tools are provided
        if tools:
            tool_instructions = """## Tool Calling Format

You have access to tools. When you need to call a tool, use this EXACT format:

<function=TOOL_NAME>{"arg1": "value1", "arg2": "value2"}</function>

Example - to remember the user's name:
<function=update_context>{"updates": {"name": "Phil"}, "reason": "User shared their name"}</function>

IMPORTANT:
- Use <function=NAME> with equals sign, not colon, slash, or parentheses
- Put valid JSON immediately after the >
- Close with </function>
- You can include normal text before or after the function call

---

"""
            # Find and modify system message - prepend instructions
            messages = list(messages)  # Copy
            for i, msg in enumerate(messages):
                if msg.get("role") == "system":
                    messages[i] = {**msg, "content": tool_instructions + msg["content"]}
                    break
            else:
                # No system message - prepend one
                messages = [{"role": "system", "content": tool_instructions.strip()}] + messages
        
        try:
            client = self._get_client()
            
            # Call cloud.llm client
            response = await client.chat(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
            )
            
            # Extract token usage
            input_tokens = response.input_tokens or 0
            output_tokens = response.output_tokens or 0
            
            info("Ollama response", input_tokens=input_tokens, output_tokens=output_tokens)
            
            # Extract tool calls if present
            tool_calls = self._parse_tool_calls(response)
            
            # XML-style tool calls are now handled at Agent level
            content = response.content or ""
            
            return build_response(
                content=content,
                model=self.model,
                provider=self.name,
                usage={"input": input_tokens, "output": output_tokens},
                tool_calls=tool_calls,
                finish_reason=response.finish_reason or "stop",
                raw=response.raw,
            )
            
        except Exception as e:
            # Map cloud.llm errors to provider errors
            errors = _get_llm_errors()
            
            if isinstance(e, errors["LLMConnectionError"]):
                error("Ollama connection error", error=str(e))
                raise ProviderError(self.name, f"Connection error: {e}")
            elif isinstance(e, errors["LLMTimeoutError"]):
                error("Ollama timeout", error=str(e))
                raise ProviderError(self.name, f"Timeout: {e}")
            elif isinstance(e, errors["LLMError"]):
                error("Ollama error", error=str(e))
                raise ProviderError(self.name, str(e))
            else:
                # Re-raise unexpected errors
                raise
    
    async def stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        """Stream completion using AsyncOllamaClient."""
        client = self._get_client()
        
        # Note: tools are ignored for streaming (Ollama doesn't support tool streaming)
        async for chunk in client.chat_stream(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield chunk
    
    def count_tokens(self, messages: list[dict]) -> int:
        """Rough estimate (Ollama doesn't expose tokenizer)."""
        total_chars = sum(len(m.get("content", "")) for m in messages)
        return total_chars // 4
    
    @property
    def max_context_tokens(self) -> int:
        """Varies by model, default to reasonable limit."""
        MODEL_LIMITS = {
            "llama3.2": 128000,
            "llama3.1": 128000,
            "mistral": 32768,
            "mixtral": 32768,
            "codellama": 16384,
            "phi3": 128000,
            "qwen2.5": 32768,
        }
        base_name = self.model.split(":")[0]
        return MODEL_LIMITS.get(base_name, 8192)
    
    async def close(self):
        """Close the underlying client."""
        if self._client is not None:
            await self._client.close()
            self._client = None
