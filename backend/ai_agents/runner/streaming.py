"""Streaming response utilities."""

from typing import AsyncIterator, Callable


class StreamBuffer:
    """Buffer for accumulating streamed content."""
    
    def __init__(self):
        self.chunks: list[str] = []
    
    def add(self, chunk: str):
        self.chunks.append(chunk)
    
    @property
    def content(self) -> str:
        return "".join(self.chunks)
    
    def clear(self):
        self.chunks.clear()


async def stream_with_callback(
    stream: AsyncIterator[str],
    callback: Callable[[str], None],
) -> str:
    """
    Stream with a callback for each chunk.
    
    Args:
        stream: Async iterator of chunks
        callback: Called with each chunk
        
    Returns:
        Complete response
    """
    buffer = StreamBuffer()
    
    async for chunk in stream:
        buffer.add(chunk)
        callback(chunk)
    
    return buffer.content


class SSEFormatter:
    """Format chunks for Server-Sent Events."""
    
    @staticmethod
    def format_chunk(chunk: str, event: str = "message") -> str:
        """Format a chunk as SSE."""
        return f"event: {event}\ndata: {chunk}\n\n"
    
    @staticmethod
    def format_done() -> str:
        """Format completion event."""
        return "event: done\ndata: [DONE]\n\n"
    
    @staticmethod
    def format_error(error: str) -> str:
        """Format error event."""
        return f"event: error\ndata: {error}\n\n"


async def stream_as_sse(stream: AsyncIterator[str]) -> AsyncIterator[str]:
    """
    Convert stream to SSE format.
    
    Usage with FastAPI:
        @app.post("/chat")
        async def chat(...):
            return StreamingResponse(
                stream_as_sse(runner.stream(...)),
                media_type="text/event-stream"
            )
    """
    formatter = SSEFormatter()
    
    try:
        async for chunk in stream:
            yield formatter.format_chunk(chunk)
        yield formatter.format_done()
    except Exception as e:
        yield formatter.format_error(str(e))
