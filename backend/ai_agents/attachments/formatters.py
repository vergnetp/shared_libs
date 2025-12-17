"""Format attachments for different LLM providers."""

import base64
from typing import Any

from shared_lib.attachments import AttachmentStore


class AttachmentFormatter:
    """Format attachments for LLM providers."""
    
    def __init__(self, store: AttachmentStore):
        self.store = store
    
    async def format_for_provider(
        self,
        path: str,
        provider: str,
    ) -> dict:
        """
        Format attachment for a specific provider.
        
        Args:
            path: Attachment path in store
            provider: Provider name (anthropic, openai, ollama)
            
        Returns:
            Provider-specific content block
        """
        formatters = {
            "anthropic": self._format_anthropic,
            "openai": self._format_openai,
            "ollama": self._format_ollama,
        }
        
        formatter = formatters.get(provider)
        if not formatter:
            raise ValueError(f"Unknown provider: {provider}")
        
        return await formatter(path)
    
    async def _format_anthropic(self, path: str) -> dict:
        """Format for Anthropic Claude."""
        content = await self.store.load(path)
        metadata = await self.store.get_metadata(path)
        mime_type = metadata.file_type if metadata else "application/octet-stream"
        
        if mime_type.startswith("image/"):
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime_type,
                    "data": base64.b64encode(content).decode(),
                }
            }
        elif mime_type == "application/pdf":
            return {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": mime_type,
                    "data": base64.b64encode(content).decode(),
                }
            }
        else:
            # Try as text
            try:
                text = content.decode("utf-8")
                return {"type": "text", "text": f"[File: {path}]\n{text}"}
            except:
                return {"type": "text", "text": f"[Binary file: {path}]"}
    
    async def _format_openai(self, path: str) -> dict:
        """Format for OpenAI."""
        content = await self.store.load(path)
        metadata = await self.store.get_metadata(path)
        mime_type = metadata.file_type if metadata else "application/octet-stream"
        
        if mime_type.startswith("image/"):
            b64 = base64.b64encode(content).decode()
            return {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{b64}",
                }
            }
        else:
            # Text content
            try:
                text = content.decode("utf-8")
                return {"type": "text", "text": f"[File: {path}]\n{text}"}
            except:
                return {"type": "text", "text": f"[Binary file: {path}]"}
    
    async def _format_ollama(self, path: str) -> dict:
        """Format for Ollama - images only, as base64."""
        content = await self.store.load(path)
        metadata = await self.store.get_metadata(path)
        mime_type = metadata.file_type if metadata else "application/octet-stream"
        
        if mime_type.startswith("image/"):
            return {
                "type": "image",
                "data": base64.b64encode(content).decode(),
            }
        else:
            try:
                text = content.decode("utf-8")
                return {"type": "text", "text": f"[File: {path}]\n{text}"}
            except:
                return {"type": "text", "text": f"[Binary file: {path}]"}


async def format_message_with_attachments(
    content: str,
    attachments: list[str],
    formatter: AttachmentFormatter,
    provider: str,
) -> list[dict]:
    """
    Format a message with attachments for a provider.
    
    Returns content blocks array (for providers that support it).
    """
    blocks = []
    
    # Add attachments first
    for path in attachments:
        block = await formatter.format_for_provider(path, provider)
        blocks.append(block)
    
    # Add text content
    if content:
        blocks.append({"type": "text", "text": content})
    
    return blocks
