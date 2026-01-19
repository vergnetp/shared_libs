"""
Anthropic Claude LLM client.

Usage:
    # Sync
    client = AnthropicClient(api_key="sk-ant-...")
    response = client.chat(
        messages=[{"role": "user", "content": "Hello!"}],
        system="You are helpful.",
    )
    
    # Async with streaming
    async with AsyncAnthropicClient(api_key="...") as client:
        async for chunk in client.chat_stream(messages):
            print(chunk, end="")
        
        # With tools
        response = await client.chat(
            messages=messages,
            tools=[{"name": "get_weather", "description": "...", "input_schema": {...}}],
        )
        if response.has_tool_calls:
            for tc in response.tool_calls:
                print(f"{tc.name}({tc.arguments})")
"""

from __future__ import annotations
from typing import List, Dict, Any, Optional, AsyncIterator
import json

from .base import BaseLLMClient, AsyncBaseLLMClient
from .types import ChatResponse, ToolCall
from .errors import LLMError, LLMConnectionError, LLMTimeoutError


ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicClient(BaseLLMClient):
    """
    Sync Anthropic Claude client.
    """
    
    PROVIDER = "anthropic"
    BASE_URL = ANTHROPIC_BASE_URL
    
    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
        timeout: float = 120.0,
        max_retries: int = 3,
    ):
        super().__init__(
            api_key=api_key,
            model=model,
            timeout=timeout,
            max_retries=max_retries,
        )
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """Anthropic uses x-api-key header."""
        return {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
    
    def chat(
        self,
        messages: List[Dict[str, Any]],
        model: str = None,
        system: str = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: List[Dict] = None,
        tool_choice: Dict = None,
        **kwargs,
    ) -> ChatResponse:
        """
        Send chat completion request.
        
        Args:
            messages: List of message dicts
            model: Model override
            system: System prompt (separate from messages in Anthropic)
            temperature: Sampling temperature (0-1)
            max_tokens: Maximum tokens to generate
            tools: Tool definitions
            tool_choice: Tool selection strategy
            **kwargs: Additional API parameters
            
        Returns:
            ChatResponse with content, usage, and tool_calls
        """
        model = model or self.model
        
        body = {
            "model": model,
            "messages": self._format_messages(messages),
            "max_tokens": max_tokens,
            **kwargs,
        }
        
        if system:
            body["system"] = system
        
        if temperature is not None:
            body["temperature"] = temperature
        
        if tools:
            body["tools"] = self._format_tools(tools)
            if tool_choice:
                body["tool_choice"] = tool_choice
        
        try:
            response = self._request("POST", "/v1/messages", json=body)
        except Exception as e:
            if "timeout" in str(e).lower():
                raise LLMTimeoutError(str(e), provider=self.PROVIDER, timeout=self.timeout)
            if "connect" in str(e).lower():
                raise LLMConnectionError(str(e), provider=self.PROVIDER)
            raise LLMError(str(e), provider=self.PROVIDER)
        
        if response.status_code >= 400:
            try:
                resp_body = response.json()
            except:
                resp_body = {}
            self._handle_error(response.status_code, resp_body, response.text)
        
        data = response.json()
        return self._parse_response(data)
    
    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        model: str = None,
        system: str = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ):
        """
        Stream chat completion (sync generator).
        
        Yields:
            Text chunks as they arrive
        """
        model = model or self.model
        
        body = {
            "model": model,
            "messages": self._format_messages(messages),
            "max_tokens": max_tokens,
            "stream": True,
            **kwargs,
        }
        
        if system:
            body["system"] = system
        
        if temperature is not None:
            body["temperature"] = temperature
        
        import requests
        
        url = f"{self._base_url}/v1/messages"
        headers = self._get_auth_headers()
        
        with requests.post(url, json=body, headers=headers, stream=True, timeout=self.timeout) as resp:
            if resp.status_code >= 400:
                try:
                    resp_body = resp.json()
                except:
                    resp_body = {}
                self._handle_error(resp.status_code, resp_body, resp.text)
            
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                        event_type = event.get("type")
                        
                        if event_type == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                if text:
                                    yield text
                    except json.JSONDecodeError:
                        continue
    
    def _format_messages(self, messages: List[Dict]) -> List[Dict]:
        """Format messages for Anthropic API."""
        formatted = []
        for m in messages:
            msg = {"role": m["role"]}
            content = m.get("content")
            
            if isinstance(content, str):
                msg["content"] = content
            elif isinstance(content, list):
                # Already formatted as content blocks
                msg["content"] = content
            else:
                msg["content"] = str(content) if content else ""
            
            formatted.append(msg)
        return formatted
    
    def _format_tools(self, tools: List[Dict]) -> List[Dict]:
        """Format tools for Anthropic API."""
        formatted = []
        for tool in tools:
            # Convert OpenAI-style to Anthropic-style
            if "function" in tool:
                func = tool["function"]
                formatted.append({
                    "name": func.get("name"),
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {}),
                })
            elif "input_schema" in tool:
                # Already Anthropic format
                formatted.append(tool)
            else:
                # Assume it's in simple format
                formatted.append({
                    "name": tool.get("name"),
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("parameters", tool.get("input_schema", {})),
                })
        return formatted
    
    def _parse_response(self, data: Dict[str, Any]) -> ChatResponse:
        """Parse Anthropic response into ChatResponse."""
        content_blocks = data.get("content", [])
        usage = data.get("usage", {})
        
        # Extract text content
        text_content = ""
        tool_calls = []
        
        for block in content_blocks:
            if block.get("type") == "text":
                text_content += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append(ToolCall.from_anthropic(block))
        
        return ChatResponse(
            content=text_content,
            model=data.get("model", self.model),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            finish_reason=data.get("stop_reason", "end_turn"),
            tool_calls=tool_calls,
            raw=data,
        )


class AsyncAnthropicClient(AsyncBaseLLMClient):
    """
    Async Anthropic Claude client with connection pooling.
    
    Usage:
        async with AsyncAnthropicClient(api_key="...") as client:
            response = await client.chat(messages, system="You are helpful.")
            
            # Streaming
            async for chunk in client.chat_stream(messages):
                print(chunk, end="")
    """
    
    PROVIDER = "anthropic"
    BASE_URL = ANTHROPIC_BASE_URL
    
    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
        timeout: float = 120.0,
        max_retries: int = 3,
    ):
        super().__init__(
            api_key=api_key,
            model=model,
            timeout=timeout,
            max_retries=max_retries,
        )
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """Anthropic uses x-api-key header."""
        return {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
    
    async def chat(
        self,
        messages: List[Dict[str, Any]],
        model: str = None,
        system: str = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: List[Dict] = None,
        tool_choice: Dict = None,
        **kwargs,
    ) -> ChatResponse:
        """
        Send async chat completion request.
        
        Args:
            messages: List of message dicts
            model: Model override
            system: System prompt
            temperature: Sampling temperature (0-1)
            max_tokens: Maximum tokens to generate
            tools: Tool definitions
            tool_choice: Tool selection strategy
            **kwargs: Additional API parameters
            
        Returns:
            ChatResponse with content, usage, and tool_calls
        """
        model = model or self.model
        
        body = {
            "model": model,
            "messages": self._format_messages(messages),
            "max_tokens": max_tokens,
            **kwargs,
        }
        
        if system:
            body["system"] = system
        
        if temperature is not None:
            body["temperature"] = temperature
        
        if tools:
            body["tools"] = self._format_tools(tools)
            if tool_choice:
                body["tool_choice"] = tool_choice
        
        try:
            response = await self._request("POST", "/v1/messages", json=body)
        except Exception as e:
            if "timeout" in str(e).lower():
                raise LLMTimeoutError(str(e), provider=self.PROVIDER, timeout=self.timeout)
            if "connect" in str(e).lower():
                raise LLMConnectionError(str(e), provider=self.PROVIDER)
            raise LLMError(str(e), provider=self.PROVIDER)
        
        if response.status_code >= 400:
            try:
                resp_body = response.json()
            except:
                resp_body = {}
            self._handle_error(response.status_code, resp_body, response.text)
        
        data = response.json()
        return self._parse_response(data)
    
    async def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        model: str = None,
        system: str = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> AsyncIterator[str]:
        """
        Stream chat completion.
        
        Yields:
            Text chunks as they arrive
        """
        client = await self._ensure_client()
        model = model or self.model
        
        body = {
            "model": model,
            "messages": self._format_messages(messages),
            "max_tokens": max_tokens,
            "stream": True,
            **kwargs,
        }
        
        if system:
            body["system"] = system
        
        if temperature is not None:
            body["temperature"] = temperature
        
        # Use pooled client's stream_sse for connection reuse and tracing
        async for event in client.stream_sse(
            "POST",
            "/v1/messages",
            json=body,
            headers=self._auth_headers,
        ):
            # Handle SSE events - event is SSEEvent dataclass with .data attribute
            if event.data == "[DONE]":
                break
            
            try:
                data = json.loads(event.data) if event.data else {}
                event_type = data.get("type")
                
                # Handle errors in stream
                if event_type == "error":
                    error = data.get("error", {})
                    raise LLMError(
                        error.get("message", str(data)),
                        provider=self.PROVIDER,
                        response_body=data,
                    )
                
                if event_type == "content_block_delta":
                    delta = data.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            yield text
            except json.JSONDecodeError:
                continue
    
    def _format_messages(self, messages: List[Dict]) -> List[Dict]:
        """Format messages for Anthropic API."""
        formatted = []
        for m in messages:
            msg = {"role": m["role"]}
            content = m.get("content")
            
            if isinstance(content, str):
                msg["content"] = content
            elif isinstance(content, list):
                msg["content"] = content
            else:
                msg["content"] = str(content) if content else ""
            
            formatted.append(msg)
        return formatted
    
    def _format_tools(self, tools: List[Dict]) -> List[Dict]:
        """Format tools for Anthropic API."""
        formatted = []
        for tool in tools:
            if "function" in tool:
                func = tool["function"]
                formatted.append({
                    "name": func.get("name"),
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {}),
                })
            elif "input_schema" in tool:
                formatted.append(tool)
            else:
                formatted.append({
                    "name": tool.get("name"),
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("parameters", tool.get("input_schema", {})),
                })
        return formatted
    
    def _parse_response(self, data: Dict[str, Any]) -> ChatResponse:
        """Parse Anthropic response into ChatResponse."""
        content_blocks = data.get("content", [])
        usage = data.get("usage", {})
        
        text_content = ""
        tool_calls = []
        
        for block in content_blocks:
            if block.get("type") == "text":
                text_content += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append(ToolCall.from_anthropic(block))
        
        return ChatResponse(
            content=text_content,
            model=data.get("model", self.model),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            finish_reason=data.get("stop_reason", "end_turn"),
            tool_calls=tool_calls,
            raw=data,
        )
