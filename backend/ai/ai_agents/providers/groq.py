"""
Groq provider using cloud.llm client.

Groq uses an OpenAI-compatible API with extremely fast inference.

Note: XML tool call parsing (Llama quirks) is handled at the Agent level
in agent.py's _parse_xml_tool_calls, not here.
"""
from __future__ import annotations

from typing import AsyncIterator

from ..core import ProviderResponse
from .base import LLMProvider
from .utils import build_response


# Model context limits
MODEL_LIMITS = {
    "llama-3.3-70b-versatile": 128000,
    "llama-3.1-8b-instant": 128000,
    "mixtral-8x7b-32768": 32768,
    "gemma2-9b-it": 8192,
}

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def _get_openai_compat_client():
    """Import AsyncOpenAICompatClient from cloud.llm (relative import)."""
    try:
        from ....cloud.llm import AsyncOpenAICompatClient
        return AsyncOpenAICompatClient
    except ImportError:
        raise ImportError(
            "cloud.llm module not found. "
            "Ensure shared_libs/backend/cloud/llm is available."
        )


class GroqProvider(LLMProvider):
    """
    Groq provider - extremely fast inference.
    
    Uses cloud.llm.AsyncOpenAICompatClient with Groq's base URL.
    
    Note: Llama models may output XML-style tool calls in content.
    These are parsed at the Agent level, not here.
    """
    
    name = "groq"
    
    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile", **kwargs):
        """
        Initialize Groq provider.
        
        Args:
            api_key: Groq API key
            model: Model name (default: llama-3.3-70b-versatile)
            **kwargs: Ignored (for compatibility)
        """
        AsyncOpenAICompatClient = _get_openai_compat_client()
        
        self.model = model
        self._client = AsyncOpenAICompatClient(
            api_key=api_key,
            model=model,
            base_url=GROQ_BASE_URL,
            timeout=60.0,  # Groq is fast
            max_retries=3,
        )
    
    async def run(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] = None,
        **kwargs,
    ) -> ProviderResponse:
        """
        Run chat completion.
        
        Args:
            messages: List of message dicts
            temperature: Sampling temperature
            max_tokens: Max tokens to generate
            tools: Tool definitions
            **kwargs: Additional params (system, etc.)
        """
        # Handle system message
        system = kwargs.get("system")
        chat_messages = list(messages)
        
        # Groq prefers system in messages array
        if system:
            chat_messages = [{"role": "system", "content": system}] + chat_messages
        
        # Normalize tool_calls in message history (Groq needs arguments as string)
        normalized_messages = []
        for m in chat_messages:
            if m.get("role") == "system":
                # Extract system for later, don't include in messages
                continue
            
            m = dict(m)  # Copy
            if m.get("tool_calls"):
                normalized_tc = []
                for tc in m["tool_calls"]:
                    tc = dict(tc)
                    if "function" in tc:
                        tc["function"] = dict(tc["function"])
                        args = tc["function"].get("arguments")
                        if args is None:
                            tc["function"]["arguments"] = "{}"
                        elif isinstance(args, dict):
                            import json
                            tc["function"]["arguments"] = json.dumps(args)
                    normalized_tc.append(tc)
                m["tool_calls"] = normalized_tc
            normalized_messages.append(m)
        
        # Call cloud.llm client
        response = await self._client.chat(
            messages=normalized_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            model=self.model,
        )
        
        # Convert ChatResponse → ProviderResponse
        tool_calls = [
            {
                "id": tc.id,
                "name": tc.name,
                "arguments": tc.arguments,
            }
            for tc in response.tool_calls
        ]
        
        return build_response(
            content=response.content,
            model=response.model,
            provider=self.name,
            usage={
                "input": response.input_tokens,
                "output": response.output_tokens,
            },
            tool_calls=tool_calls,
            finish_reason=response.finish_reason,
            raw=response.raw,
        )
    
    async def stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        """
        Stream chat completion.
        
        Note: Tools not supported in streaming mode.
        
        Yields:
            Text chunks as they arrive
        """
        system = kwargs.get("system")
        chat_messages = list(messages)
        
        if system:
            chat_messages = [{"role": "system", "content": system}] + chat_messages
        
        # Filter system from messages for Groq
        chat_messages = [m for m in chat_messages if m.get("role") != "system"]
        
        async for chunk in self._client.chat_stream(
            messages=chat_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            model=self.model,
        ):
            yield chunk
    
    def count_tokens(self, messages: list[dict]) -> int:
        """
        Count tokens in messages.
        
        Uses heuristic (Llama tokenizer not easily available).
        """
        total_chars = sum(
            len(m.get("content", "")) 
            for m in messages 
            if isinstance(m.get("content"), str)
        )
        return total_chars // 4
    
    @property
    def max_context_tokens(self) -> int:
        """Max context window for this model."""
        return MODEL_LIMITS.get(self.model, 32000)
    
    async def close(self):
        """Close the underlying client."""
        await self._client.close()
