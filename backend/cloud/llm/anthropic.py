"""
Anthropic Claude LLM Client.

Usage:
    # Sync
    client = AnthropicClient(api_key="sk-ant-...")
    response = client.chat(
        messages=[{"role": "user", "content": "Hello"}],
        system="You are helpful.",
    )
    
    # Streaming
    for chunk in client.chat_stream(messages, system="Be concise"):
        print(chunk, end="")
    
    # Async
    async with AsyncAnthropicClient(api_key="...") as client:
        response = await client.chat(messages)
        
        async for chunk in client.chat_stream(messages):
            print(chunk, end="")
"""

from __future__ import annotations
import json
from typing import Iterator, AsyncIterator, Any

from .types import ChatResponse, ToolCall


ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"


def _parse_sse_line(line: str) -> tuple[str, dict | None]:
    """
    Parse Anthropic SSE line.
    
    Returns: (event_type, data_dict or None)
    """
    line = line.strip()
    if not line:
        return "", None
    
    if line.startswith("event: "):
        return line[7:], None
    
    if line.startswith("data: "):
        try:
            return "", json.loads(line[6:])
        except json.JSONDecodeError:
            return "", None
    
    return "", None


def _convert_messages(messages: list[dict]) -> list[dict]:
    """
    Convert messages to Anthropic format.
    
    Handles:
    - Filtering out system messages (passed separately)
    - Converting tool results
    - Converting assistant messages with tool_calls
    """
    result = []
    
    # Collect tool_result IDs to know which tool_use blocks have results
    tool_result_ids = set()
    for m in messages:
        if m.get("role") == "tool" and m.get("tool_call_id"):
            tool_result_ids.add(m["tool_call_id"])
    
    for m in messages:
        role = m.get("role")
        
        # Skip system messages - passed separately to Anthropic
        if role == "system":
            continue
        
        # Convert tool results to user message with tool_result block
        if role == "tool":
            result.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": m["tool_call_id"],
                    "content": m["content"],
                }]
            })
            continue
        
        # Convert assistant message with tool_calls
        if role == "assistant" and m.get("tool_calls"):
            content_blocks = []
            
            # Add text content if present
            if m.get("content"):
                content_blocks.append({"type": "text", "text": m["content"]})
            
            # Add tool_use blocks
            for tc in m["tool_calls"]:
                # Handle both OpenAI format and internal format
                if "function" in tc:
                    # OpenAI format
                    func = tc["function"]
                    args = func.get("arguments", "{}")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    
                    tc_id = tc.get("id", "")
                    # Skip orphaned tool_use (no corresponding result)
                    if tc_id and tc_id not in tool_result_ids:
                        continue
                    
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc_id,
                        "name": func["name"],
                        "input": args,
                    })
                else:
                    # Internal format
                    tc_id = tc.get("id", "")
                    if tc_id and tc_id not in tool_result_ids:
                        continue
                    
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc_id,
                        "name": tc["name"],
                        "input": tc.get("arguments", {}),
                    })
            
            if content_blocks:
                result.append({"role": "assistant", "content": content_blocks})
            elif m.get("content"):
                # All tool_use were orphaned, just add text
                result.append({"role": "assistant", "content": m["content"]})
            continue
        
        # Regular message - pass through
        result.append({"role": role, "content": m.get("content", "")})
    
    return result


def _convert_tools(tools: list[dict]) -> list[dict]:
    """Convert tool definitions to Anthropic format."""
    return [
        {
            "name": t["name"],
            "description": t.get("description", ""),
            "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
        }
        for t in tools
    ]


def _parse_response(data: dict, model: str) -> ChatResponse:
    """Parse Anthropic messages response."""
    content = ""
    tool_calls = []
    
    for block in data.get("content", []):
        if block.get("type") == "text":
            content += block.get("text", "")
        elif block.get("type") == "tool_use":
            tool_calls.append(ToolCall.from_anthropic(block))
    
    usage = data.get("usage", {})
    
    return ChatResponse(
        content=content,
        model=data.get("model", model),
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        finish_reason=data.get("stop_reason", "end_turn"),
        tool_calls=tool_calls,
        raw=data,
    )


class AnthropicClient:
    """
    Synchronous Anthropic Claude client.
    
    Uses http_client for non-streaming (retries, circuit breaker).
    Uses httpx directly for streaming.
    """
    
    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
        timeout: float = 120.0,
        max_retries: int = 3,
    ):
        """
        Initialize Anthropic client.
        
        Args:
            api_key: Anthropic API key
            model: Default model to use
            timeout: Request timeout in seconds
            max_retries: Max retry attempts for non-streaming requests
        """
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        
        self._http_client = None
    
    def _get_http_client(self):
        """Get or create the HTTP client."""
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
                    retry_on_status={429, 500, 502, 503, 504, 529},  # 529 = overloaded
                ),
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "Content-Type": "application/json",
                },
            )
            
            self._http_client = SyncHttpClient(
                config=config,
                base_url=ANTHROPIC_BASE_URL,
            )
        
        return self._http_client
    
    def chat(
        self,
        messages: list[dict],
        system: str | None = None,
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
            system: System prompt (passed separately in Anthropic API)
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate
            tools: Optional list of tool definitions
            model: Override default model
            **kwargs: Additional API parameters
            
        Returns:
            ChatResponse with content, usage, and any tool calls
        """
        use_model = model or self.model
        
        # Extract system from messages if not provided explicitly
        if system is None:
            for m in messages:
                if m.get("role") == "system":
                    system = m["content"]
                    break
        
        # Convert messages to Anthropic format
        anthropic_messages = _convert_messages(messages)
        
        payload = {
            "model": use_model,
            "messages": anthropic_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        if system:
            payload["system"] = system
        
        if tools:
            payload["tools"] = _convert_tools(tools)
        
        # Pass through extra params
        for key in ("stop_sequences", "top_k", "top_p", "metadata"):
            if key in kwargs:
                payload[key] = kwargs[key]
        
        client = self._get_http_client()
        response = client.post("/v1/messages", json=payload)
        
        return _parse_response(response.json(), use_model)
    
    def chat_stream(
        self,
        messages: list[dict],
        system: str | None = None,
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
            system: System prompt
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            model: Override default model
            
        Yields:
            Text chunks as they arrive
        """
        import httpx
        
        use_model = model or self.model
        
        # Extract system from messages if not provided
        if system is None:
            for m in messages:
                if m.get("role") == "system":
                    system = m["content"]
                    break
        
        anthropic_messages = _convert_messages(messages)
        
        payload = {
            "model": use_model,
            "messages": anthropic_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        
        if system:
            payload["system"] = system
        
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }
        
        with httpx.Client(timeout=self.timeout) as client:
            with client.stream(
                "POST",
                f"{ANTHROPIC_BASE_URL}/v1/messages",
                json=payload,
                headers=headers,
            ) as response:
                response.raise_for_status()
                
                for line in response.iter_lines():
                    event_type, data = _parse_sse_line(line)
                    if data and data.get("type") == "content_block_delta":
                        delta = data.get("delta", {})
                        if delta.get("type") == "text_delta":
                            yield delta.get("text", "")
    
    def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http_client:
            self._http_client.close()
            self._http_client = None
    
    def __enter__(self) -> "AnthropicClient":
        return self
    
    def __exit__(self, *args) -> None:
        self.close()


class AsyncAnthropicClient:
    """
    Asynchronous Anthropic Claude client.
    
    Uses http_client for non-streaming (retries, circuit breaker).
    Uses aiohttp directly for streaming.
    """
    
    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
        timeout: float = 120.0,
        max_retries: int = 3,
    ):
        """
        Initialize async Anthropic client.
        
        Args:
            api_key: Anthropic API key
            model: Default model to use
            timeout: Request timeout in seconds
            max_retries: Max retry attempts for non-streaming requests
        """
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        
        self._http_client = None
    
    def _get_http_client(self):
        """Get or create the HTTP client."""
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
                    retry_on_status={429, 500, 502, 503, 504, 529},
                ),
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "Content-Type": "application/json",
                },
            )
            
            self._http_client = AsyncHttpClient(
                config=config,
                base_url=ANTHROPIC_BASE_URL,
            )
        
        return self._http_client
    
    async def chat(
        self,
        messages: list[dict],
        system: str | None = None,
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
            system: System prompt (passed separately in Anthropic API)
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate
            tools: Optional list of tool definitions
            model: Override default model
            **kwargs: Additional API parameters
            
        Returns:
            ChatResponse with content, usage, and any tool calls
        """
        use_model = model or self.model
        
        # Extract system from messages if not provided
        if system is None:
            for m in messages:
                if m.get("role") == "system":
                    system = m["content"]
                    break
        
        anthropic_messages = _convert_messages(messages)
        
        payload = {
            "model": use_model,
            "messages": anthropic_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        if system:
            payload["system"] = system
        
        if tools:
            payload["tools"] = _convert_tools(tools)
        
        for key in ("stop_sequences", "top_k", "top_p", "metadata"):
            if key in kwargs:
                payload[key] = kwargs[key]
        
        client = self._get_http_client()
        response = await client.post("/v1/messages", json=payload)
        
        return _parse_response(response.json(), use_model)
    
    async def chat_stream(
        self,
        messages: list[dict],
        system: str | None = None,
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
            system: System prompt
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            model: Override default model
            
        Yields:
            Text chunks as they arrive
        """
        import aiohttp
        
        use_model = model or self.model
        
        # Extract system from messages if not provided
        if system is None:
            for m in messages:
                if m.get("role") == "system":
                    system = m["content"]
                    break
        
        anthropic_messages = _convert_messages(messages)
        
        payload = {
            "model": use_model,
            "messages": anthropic_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        
        if system:
            payload["system"] = system
        
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }
        
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{ANTHROPIC_BASE_URL}/v1/messages",
                json=payload,
                headers=headers,
            ) as response:
                response.raise_for_status()
                
                buffer = ""
                async for chunk in response.content:
                    buffer += chunk.decode("utf-8")
                    
                    # Process complete lines
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        event_type, data = _parse_sse_line(line)
                        if data and data.get("type") == "content_block_delta":
                            delta = data.get("delta", {})
                            if delta.get("type") == "text_delta":
                                yield delta.get("text", "")
    
    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http_client:
            await self._http_client.close()
            self._http_client = None
    
    async def __aenter__(self) -> "AsyncAnthropicClient":
        return self
    
    async def __aexit__(self, *args) -> None:
        await self.close()
