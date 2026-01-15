"""
OpenAI-Compatible LLM Client.

Works with:
- OpenAI (api.openai.com)
- Groq (api.groq.com/openai/v1)
- Together (api.together.xyz/v1)
- Any OpenAI-compatible API

Usage:
    # OpenAI
    client = OpenAICompatClient(api_key="sk-...", model="gpt-4o")
    response = client.chat([{"role": "user", "content": "Hello"}])
    
    # Groq
    client = OpenAICompatClient(
        api_key="gsk-...",
        base_url="https://api.groq.com/openai/v1",
        model="llama-3.3-70b-versatile"
    )
    
    # Streaming
    for chunk in client.chat_stream(messages):
        print(chunk, end="")
    
    # Async
    async with AsyncOpenAICompatClient(api_key="...") as client:
        response = await client.chat(messages)
        
        async for chunk in client.chat_stream(messages):
            print(chunk, end="")
"""

from __future__ import annotations
import json
from typing import Iterator, AsyncIterator, Any

from .types import ChatResponse, ToolCall


# Default base URL
OPENAI_BASE_URL = "https://api.openai.com/v1"


def _parse_sse_line(line: str) -> dict | None:
    """Parse a single SSE line, return data dict or None."""
    line = line.strip()
    if not line or not line.startswith("data: "):
        return None
    data = line[6:]  # Remove "data: " prefix
    if data == "[DONE]":
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None


def _build_chat_payload(
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    tools: list[dict] | None,
    stream: bool = False,
    **kwargs,
) -> dict:
    """Build the request payload for chat completions."""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    
    if tools:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters", {"type": "object", "properties": {}}),
                }
            }
            for t in tools
        ]
        payload["tool_choice"] = kwargs.get("tool_choice", "auto")
    
    # Pass through any extra params
    for key in ("stop", "presence_penalty", "frequency_penalty", "logit_bias", "user"):
        if key in kwargs:
            payload[key] = kwargs[key]
    
    return payload


def _parse_chat_response(data: dict, model: str) -> ChatResponse:
    """Parse OpenAI chat completion response."""
    choice = data["choices"][0]
    message = choice["message"]
    
    # Parse tool calls if present
    tool_calls = []
    if message.get("tool_calls"):
        for tc in message["tool_calls"]:
            tool_calls.append(ToolCall.from_openai(tc))
    
    usage = data.get("usage", {})
    
    return ChatResponse(
        content=message.get("content") or "",
        model=data.get("model", model),
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
        finish_reason=choice.get("finish_reason", "stop"),
        tool_calls=tool_calls,
        raw=data,
    )


class OpenAICompatClient:
    """
    Synchronous OpenAI-compatible client.
    
    Uses http_client for non-streaming (retries, circuit breaker).
    Uses httpx directly for streaming.
    """
    
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: str = OPENAI_BASE_URL,
        timeout: float = 120.0,
        max_retries: int = 3,
    ):
        """
        Initialize OpenAI-compatible client.
        
        Args:
            api_key: API key for authentication
            model: Default model to use
            base_url: API base URL (change for Groq, Together, etc.)
            timeout: Request timeout in seconds
            max_retries: Max retry attempts for non-streaming requests
        """
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        
        # Lazy-init http_client for non-streaming
        self._http_client = None
    
    def _get_http_client(self):
        """Get or create the HTTP client for non-streaming requests."""
        if self._http_client is None:
            from ...http_client import (
                SyncHttpClient,
                HttpConfig,
                RetryConfig,
            )
            
            config = HttpConfig(
                timeout=self.timeout,
                retry=RetryConfig(
                    max_retries=self.max_retries,
                    base_delay=1.0,
                    retry_on_status={429, 500, 502, 503, 504},
                ),
            )
            
            self._http_client = SyncHttpClient(
                config=config,
                base_url=self.base_url,
            )
            self._http_client.set_bearer_token(self.api_key)
        
        return self._http_client
    
    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
        model: str | None = None,
        **kwargs,
    ) -> ChatResponse:
        """
        Send a chat completion request.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0.0-2.0)
            max_tokens: Maximum tokens to generate
            tools: Optional list of tool definitions
            model: Override default model
            **kwargs: Additional API parameters
            
        Returns:
            ChatResponse with content, usage, and any tool calls
        """
        use_model = model or self.model
        payload = _build_chat_payload(
            model=use_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            stream=False,
            **kwargs,
        )
        
        client = self._get_http_client()
        response = client.post("/chat/completions", json=payload)
        
        return _parse_chat_response(response.json(), use_model)
    
    def chat_stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: str | None = None,
        **kwargs,
    ) -> Iterator[str]:
        """
        Stream a chat completion, yielding text chunks.
        
        Note: Tools are not supported in streaming mode.
        
        Args:
            messages: List of message dicts
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            model: Override default model
            
        Yields:
            Text chunks as they arrive
        """
        import httpx
        
        use_model = model or self.model
        payload = _build_chat_payload(
            model=use_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=None,  # No tools in streaming
            stream=True,
            **kwargs,
        )
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        with httpx.Client(timeout=self.timeout) as client:
            with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
            ) as response:
                response.raise_for_status()
                
                for line in response.iter_lines():
                    data = _parse_sse_line(line)
                    if data:
                        delta = data.get("choices", [{}])[0].get("delta", {})
                        if content := delta.get("content"):
                            yield content
    
    def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http_client:
            self._http_client.close()
            self._http_client = None
    
    def __enter__(self) -> "OpenAICompatClient":
        return self
    
    def __exit__(self, *args) -> None:
        self.close()


class AsyncOpenAICompatClient:
    """
    Asynchronous OpenAI-compatible client.
    
    Uses http_client for non-streaming (retries, circuit breaker).
    Uses aiohttp directly for streaming.
    """
    
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: str = OPENAI_BASE_URL,
        timeout: float = 120.0,
        max_retries: int = 3,
    ):
        """
        Initialize async OpenAI-compatible client.
        
        Args:
            api_key: API key for authentication
            model: Default model to use
            base_url: API base URL (change for Groq, Together, etc.)
            timeout: Request timeout in seconds
            max_retries: Max retry attempts for non-streaming requests
        """
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        
        # Lazy-init http_client for non-streaming
        self._http_client = None
    
    def _get_http_client(self):
        """Get or create the HTTP client for non-streaming requests."""
        if self._http_client is None:
            from ...http_client import (
                AsyncHttpClient,
                HttpConfig,
                RetryConfig,
            )
            
            config = HttpConfig(
                timeout=self.timeout,
                retry=RetryConfig(
                    max_retries=self.max_retries,
                    base_delay=1.0,
                    retry_on_status={429, 500, 502, 503, 504},
                ),
            )
            
            self._http_client = AsyncHttpClient(
                config=config,
                base_url=self.base_url,
            )
            self._http_client.set_bearer_token(self.api_key)
        
        return self._http_client
    
    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
        model: str | None = None,
        **kwargs,
    ) -> ChatResponse:
        """
        Send a chat completion request.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0.0-2.0)
            max_tokens: Maximum tokens to generate
            tools: Optional list of tool definitions
            model: Override default model
            **kwargs: Additional API parameters
            
        Returns:
            ChatResponse with content, usage, and any tool calls
        """
        use_model = model or self.model
        payload = _build_chat_payload(
            model=use_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            stream=False,
            **kwargs,
        )
        
        client = self._get_http_client()
        response = await client.post("/chat/completions", json=payload)
        
        return _parse_chat_response(response.json(), use_model)
    
    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: str | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        """
        Stream a chat completion, yielding text chunks.
        
        Note: Tools are not supported in streaming mode.
        
        Args:
            messages: List of message dicts
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            model: Override default model
            
        Yields:
            Text chunks as they arrive
        """
        import aiohttp
        
        use_model = model or self.model
        payload = _build_chat_payload(
            model=use_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=None,  # No tools in streaming
            stream=True,
            **kwargs,
        )
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
            ) as response:
                response.raise_for_status()
                
                async for line in response.content:
                    line = line.decode("utf-8")
                    data = _parse_sse_line(line)
                    if data:
                        delta = data.get("choices", [{}])[0].get("delta", {})
                        if content := delta.get("content"):
                            yield content
    
    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http_client:
            await self._http_client.close()
            self._http_client = None
    
    async def __aenter__(self) -> "AsyncOpenAICompatClient":
        return self
    
    async def __aexit__(self, *args) -> None:
        await self.close()
