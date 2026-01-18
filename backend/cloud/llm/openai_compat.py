"""
OpenAI-compatible LLM client.

Works with:
- OpenAI API
- Groq (https://api.groq.com/openai/v1)
- Azure OpenAI
- Any OpenAI-compatible API

Usage:
    # OpenAI
    client = OpenAICompatClient(api_key="sk-...", model="gpt-4o")
    response = client.chat([{"role": "user", "content": "Hello!"}])
    
    # Groq
    client = OpenAICompatClient(
        api_key="gsk-...",
        base_url="https://api.groq.com/openai/v1",
        model="llama-3.3-70b-versatile"
    )
    
    # Async
    async with AsyncOpenAICompatClient(api_key="...") as client:
        response = await client.chat(messages)
        
        # Streaming
        async for chunk in client.chat_stream(messages):
            print(chunk, end="")
"""

from __future__ import annotations
from typing import List, Dict, Any, Optional, AsyncIterator
import json

from .base import BaseLLMClient, AsyncBaseLLMClient, _stream_sse
from .types import ChatResponse, ToolCall
from .errors import LLMError, LLMConnectionError, LLMTimeoutError


OPENAI_BASE_URL = "https://api.openai.com/v1"


class OpenAICompatClient(BaseLLMClient):
    """
    Sync OpenAI-compatible client.
    
    Works with OpenAI, Groq, Azure OpenAI, and other compatible APIs.
    """
    
    PROVIDER = "openai"
    BASE_URL = OPENAI_BASE_URL
    
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: str = None,
        timeout: float = 120.0,
        max_retries: int = 3,
    ):
        # Determine provider from base_url
        if base_url and "groq" in base_url.lower():
            self.PROVIDER = "groq"
        
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
        )
    
    def chat(
        self,
        messages: List[Dict[str, Any]],
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: List[Dict] = None,
        tool_choice: str = None,
        **kwargs,
    ) -> ChatResponse:
        """
        Send chat completion request.
        
        Args:
            messages: List of message dicts [{"role": "user", "content": "..."}]
            model: Model override (uses instance default if not set)
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens to generate
            tools: Tool definitions for function calling
            tool_choice: How to select tools ("auto", "none", or specific)
            **kwargs: Additional API parameters
            
        Returns:
            ChatResponse with content, usage, and tool_calls
        """
        model = model or self.model
        
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **kwargs,
        }
        
        if tools:
            body["tools"] = self._format_tools(tools)
            if tool_choice:
                body["tool_choice"] = tool_choice
        
        try:
            response = self._client.request("POST", "/chat/completions", json=body)
        except Exception as e:
            if "timeout" in str(e).lower():
                raise LLMTimeoutError(str(e), provider=self.PROVIDER, timeout=self.timeout)
            if "connect" in str(e).lower():
                raise LLMConnectionError(str(e), provider=self.PROVIDER)
            raise LLMError(str(e), provider=self.PROVIDER)
        
        if response.status_code >= 400:
            try:
                body = response.json()
            except:
                body = {}
            self._handle_error(response.status_code, body, response.text)
        
        data = response.json()
        return self._parse_response(data)
    
    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ):
        """
        Stream chat completion (sync generator).
        
        Note: Tool calls not supported in streaming mode.
        
        Yields:
            Text chunks as they arrive
        """
        model = model or self.model
        
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            **kwargs,
        }
        
        import requests
        
        url = f"{self._base_url}/chat/completions"
        headers = self._get_auth_headers()
        headers["Content-Type"] = "application/json"
        
        with requests.post(url, json=body, headers=headers, stream=True, timeout=self.timeout) as resp:
            if resp.status_code >= 400:
                try:
                    body = resp.json()
                except:
                    body = {}
                self._handle_error(resp.status_code, body, resp.text)
            
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue
    
    def _format_tools(self, tools: List[Dict]) -> List[Dict]:
        """Format tools for OpenAI API."""
        formatted = []
        for tool in tools:
            if "type" not in tool:
                # Wrap in function format
                formatted.append({
                    "type": "function",
                    "function": tool,
                })
            else:
                formatted.append(tool)
        return formatted
    
    def _parse_response(self, data: Dict[str, Any]) -> ChatResponse:
        """Parse OpenAI response into ChatResponse."""
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        usage = data.get("usage", {})
        
        # Parse tool calls
        tool_calls = []
        if message.get("tool_calls"):
            for tc in message["tool_calls"]:
                tool_calls.append(ToolCall.from_openai(tc))
        
        return ChatResponse(
            content=message.get("content") or "",
            model=data.get("model", self.model),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            finish_reason=choice.get("finish_reason", "stop"),
            tool_calls=tool_calls,
            raw=data,
        )


class AsyncOpenAICompatClient(AsyncBaseLLMClient):
    """
    Async OpenAI-compatible client with connection pooling.
    
    Works with OpenAI, Groq, Azure OpenAI, and other compatible APIs.
    
    Usage:
        async with AsyncOpenAICompatClient(api_key="...") as client:
            response = await client.chat(messages)
            
            # Streaming
            async for chunk in client.chat_stream(messages):
                print(chunk, end="")
    """
    
    PROVIDER = "openai"
    BASE_URL = OPENAI_BASE_URL
    
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: str = None,
        timeout: float = 120.0,
        max_retries: int = 3,
    ):
        # Determine provider from base_url
        if base_url and "groq" in base_url.lower():
            self.PROVIDER = "groq"
        
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
        )
    
    async def chat(
        self,
        messages: List[Dict[str, Any]],
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: List[Dict] = None,
        tool_choice: str = None,
        **kwargs,
    ) -> ChatResponse:
        """
        Send async chat completion request.
        
        Args:
            messages: List of message dicts
            model: Model override
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens to generate
            tools: Tool definitions for function calling
            tool_choice: How to select tools
            **kwargs: Additional API parameters
            
        Returns:
            ChatResponse with content, usage, and tool_calls
        """
        client = await self._ensure_client()
        model = model or self.model
        
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **kwargs,
        }
        
        if tools:
            body["tools"] = self._format_tools(tools)
            if tool_choice:
                body["tool_choice"] = tool_choice
        
        try:
            response = await client.request("POST", "/chat/completions", json=body)
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
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> AsyncIterator[str]:
        """
        Stream chat completion.
        
        Note: Tool calls not supported in streaming mode.
        
        Yields:
            Text chunks as they arrive
        """
        import httpx
        
        model = model or self.model
        
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            **kwargs,
        }
        
        headers = self._get_auth_headers()
        headers["Content-Type"] = "application/json"
        
        # Use direct httpx for streaming (pool doesn't support streaming well)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                json=body,
                headers=headers,
            ) as response:
                if response.status_code >= 400:
                    text = await response.aread()
                    try:
                        resp_body = json.loads(text)
                    except:
                        resp_body = {}
                    self._handle_error(response.status_code, resp_body, text.decode() if isinstance(text, bytes) else text)
                
                async for chunk_data in _stream_sse(response):
                    delta = chunk_data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
    
    def _format_tools(self, tools: List[Dict]) -> List[Dict]:
        """Format tools for OpenAI API."""
        formatted = []
        for tool in tools:
            if "type" not in tool:
                formatted.append({
                    "type": "function",
                    "function": tool,
                })
            else:
                formatted.append(tool)
        return formatted
    
    def _parse_response(self, data: Dict[str, Any]) -> ChatResponse:
        """Parse OpenAI response into ChatResponse."""
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        usage = data.get("usage", {})
        
        tool_calls = []
        if message.get("tool_calls"):
            for tc in message["tool_calls"]:
                tool_calls.append(ToolCall.from_openai(tc))
        
        return ChatResponse(
            content=message.get("content") or "",
            model=data.get("model", self.model),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            finish_reason=choice.get("finish_reason", "stop"),
            tool_calls=tool_calls,
            raw=data,
        )
