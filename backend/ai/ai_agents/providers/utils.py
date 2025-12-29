"""
Shared utilities for LLM providers.

Centralizes common logic for tool call parsing, response building, etc.
"""
from __future__ import annotations

import json
from typing import Any

from ..core import ProviderResponse


def parse_tool_args(args: str | dict | None) -> dict:
    """
    Parse tool call arguments from various formats to dict.
    
    Handles: None, empty string, JSON string, or dict.
    """
    if args is None:
        return {}
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        if not args.strip():
            return {}
        try:
            return json.loads(args)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def normalize_tool_call(
    tc_id: str | None,
    name: str,
    arguments: str | dict | None,
    fallback_id_prefix: str = "call",
) -> dict:
    """
    Normalize a tool call to standard format.
    
    Returns: {"id": str, "name": str, "arguments": dict}
    """
    return {
        "id": tc_id or f"{fallback_id_prefix}_{id(arguments) & 0xFFFF}",
        "name": name,
        "arguments": parse_tool_args(arguments),
    }


def parse_openai_tool_calls(tool_calls_raw: list | None, extra_fields: dict | None = None) -> list[dict]:
    """
    Parse tool calls from OpenAI/Groq format.
    
    Input format: [{"id": "...", "function": {"name": "...", "arguments": "..."}}]
    
    Args:
        tool_calls_raw: Raw tool calls from API
        extra_fields: Optional dict of fields to add to each tool call
    """
    if not tool_calls_raw:
        return []
    
    result = []
    for tc in tool_calls_raw:
        # Handle both object and dict forms
        if hasattr(tc, "function"):
            # Object form (from SDK)
            parsed = normalize_tool_call(
                tc_id=tc.id,
                name=tc.function.name,
                arguments=tc.function.arguments,
            )
        elif isinstance(tc, dict):
            # Dict form
            func = tc.get("function", {})
            parsed = normalize_tool_call(
                tc_id=tc.get("id"),
                name=func.get("name", tc.get("name", "")),
                arguments=func.get("arguments", tc.get("arguments")),
            )
        else:
            continue
        
        # Add extra fields if provided
        if extra_fields:
            parsed.update(extra_fields)
        
        result.append(parsed)
    
    return result


def parse_anthropic_tool_calls(content_blocks: list | None) -> list[dict]:
    """
    Parse tool calls from Anthropic format.
    
    Input format: [{"type": "tool_use", "id": "...", "name": "...", "input": {...}}]
    """
    if not content_blocks:
        return []
    
    result = []
    for block in content_blocks:
        if hasattr(block, "type") and block.type == "tool_use":
            result.append(normalize_tool_call(
                tc_id=block.id,
                name=block.name,
                arguments=block.input,
            ))
        elif isinstance(block, dict) and block.get("type") == "tool_use":
            result.append(normalize_tool_call(
                tc_id=block.get("id"),
                name=block.get("name", ""),
                arguments=block.get("input"),
            ))
    return result


def parse_ollama_tool_calls(tool_calls_raw: list | None) -> list[dict]:
    """
    Parse tool calls from Ollama format.
    
    Input format: [{"function": {"name": "...", "arguments": {...}}}]
    """
    if not tool_calls_raw:
        return []
    
    result = []
    for i, tc in enumerate(tool_calls_raw):
        func = tc.get("function", {})
        result.append(normalize_tool_call(
            tc_id=tc.get("id", f"ollama_{i}"),
            name=func.get("name", ""),
            arguments=func.get("arguments"),
        ))
    return result


def build_response(
    content: str,
    model: str,
    provider: str,
    usage: dict | None = None,
    tool_calls: list[dict] | None = None,
    finish_reason: str | None = None,
    raw: Any = None,
) -> ProviderResponse:
    """Build standardized ProviderResponse."""
    return ProviderResponse(
        content=content or "",
        usage=usage or {"input": 0, "output": 0},
        model=model,
        provider=provider,
        tool_calls=tool_calls or [],
        finish_reason=finish_reason,
        raw=raw,
    )
