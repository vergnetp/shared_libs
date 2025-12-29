"""Text chunking strategies for document processing."""

import re
from typing import List, Iterator, Callable
from dataclasses import dataclass


@dataclass
class Chunk:
    """A text chunk with metadata."""
    text: str
    index: int
    page_num: int = None
    start_char: int = None
    end_char: int = None
    
    def __len__(self):
        return len(self.text)


class ChunkingStrategy:
    """Base class for chunking strategies."""
    
    def chunk(self, text: str, page_num: int = None) -> List[Chunk]:
        """Split text into chunks."""
        raise NotImplementedError


class SentenceChunker(ChunkingStrategy):
    """
    Chunk by sentences, respecting max size.
    
    Tries to keep sentences together while staying under max_chars.
    Good for general documents.
    """
    
    # Sentence boundary pattern
    SENTENCE_END = re.compile(r'(?<=[.!?])\s+(?=[A-Z])')
    
    def __init__(
        self,
        max_chars: int = 900,
        min_chars: int = 100,
        overlap_chars: int = 100,
    ):
        """
        Args:
            max_chars: Maximum characters per chunk
            min_chars: Minimum characters per chunk (avoid tiny chunks)
            overlap_chars: Characters to overlap between chunks
        """
        self.max_chars = max_chars
        self.min_chars = min_chars
        self.overlap_chars = overlap_chars
    
    def chunk(self, text: str, page_num: int = None) -> List[Chunk]:
        """Split text into sentence-based chunks."""
        if not text or not text.strip():
            return []
        
        # Split into sentences
        sentences = self.SENTENCE_END.split(text)
        sentences = [s.strip() for s in sentences if s.strip()]
        
        if not sentences:
            return [Chunk(text=text.strip(), index=0, page_num=page_num)]
        
        chunks = []
        current_chunk = []
        current_len = 0
        chunk_index = 0
        
        for sentence in sentences:
            sentence_len = len(sentence)
            
            # Would this sentence push us over the limit?
            if current_len + sentence_len > self.max_chars and current_chunk:
                # Save current chunk
                chunk_text = " ".join(current_chunk)
                chunks.append(Chunk(
                    text=chunk_text,
                    index=chunk_index,
                    page_num=page_num,
                ))
                chunk_index += 1
                
                # Start new chunk with overlap
                if self.overlap_chars > 0:
                    # Keep last sentence(s) for overlap
                    overlap_text = ""
                    for s in reversed(current_chunk):
                        if len(overlap_text) + len(s) < self.overlap_chars:
                            overlap_text = s + " " + overlap_text
                        else:
                            break
                    current_chunk = [overlap_text.strip()] if overlap_text.strip() else []
                    current_len = len(overlap_text)
                else:
                    current_chunk = []
                    current_len = 0
            
            current_chunk.append(sentence)
            current_len += sentence_len + 1  # +1 for space
        
        # Don't forget the last chunk
        if current_chunk:
            chunk_text = " ".join(current_chunk)
            if len(chunk_text) >= self.min_chars or not chunks:
                chunks.append(Chunk(
                    text=chunk_text,
                    index=chunk_index,
                    page_num=page_num,
                ))
            elif chunks:
                # Append to last chunk if too small
                last_chunk = chunks[-1]
                chunks[-1] = Chunk(
                    text=last_chunk.text + " " + chunk_text,
                    index=last_chunk.index,
                    page_num=last_chunk.page_num,
                )
        
        return chunks


class TokenChunker(ChunkingStrategy):
    """
    Chunk by token count (for embedding model limits).
    
    Uses a tokenizer to ensure chunks fit within model's context window.
    """
    
    def __init__(
        self,
        max_tokens: int = 512,
        overlap_tokens: int = 50,
        tokenizer: Callable[[str], List[str]] = None,
    ):
        """
        Args:
            max_tokens: Maximum tokens per chunk
            overlap_tokens: Tokens to overlap between chunks
            tokenizer: Function to tokenize text (default: whitespace)
        """
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self.tokenizer = tokenizer or (lambda t: t.split())
    
    def chunk(self, text: str, page_num: int = None) -> List[Chunk]:
        """Split text into token-based chunks."""
        if not text or not text.strip():
            return []
        
        tokens = self.tokenizer(text)
        
        if len(tokens) <= self.max_tokens:
            return [Chunk(text=text.strip(), index=0, page_num=page_num)]
        
        chunks = []
        chunk_index = 0
        i = 0
        
        while i < len(tokens):
            # Take max_tokens
            end = min(i + self.max_tokens, len(tokens))
            chunk_tokens = tokens[i:end]
            
            chunks.append(Chunk(
                text=" ".join(chunk_tokens),
                index=chunk_index,
                page_num=page_num,
            ))
            chunk_index += 1
            
            # Move forward with overlap
            i += self.max_tokens - self.overlap_tokens
        
        return chunks


class CrossPageChunker:
    """
    Chunk across page boundaries for better context.
    
    Handles documents where information spans multiple pages.
    """
    
    def __init__(
        self,
        base_chunker: ChunkingStrategy = None,
        page_separator: str = "\n\n---PAGE BREAK---\n\n",
    ):
        """
        Args:
            base_chunker: Chunker to use (default: SentenceChunker)
            page_separator: String to join pages before chunking
        """
        self.base_chunker = base_chunker or SentenceChunker()
        self.page_separator = page_separator
    
    def chunk_pages(self, pages: List[str]) -> List[Chunk]:
        """
        Chunk multiple pages together.
        
        Args:
            pages: List of page texts
            
        Returns:
            Chunks that may span pages
        """
        # Join all pages
        full_text = self.page_separator.join(pages)
        
        # Chunk the combined text
        chunks = self.base_chunker.chunk(full_text)
        
        # Try to determine page numbers for each chunk
        for chunk in chunks:
            # Count page separators before this chunk's position
            # This is approximate - could be improved
            chunk.page_num = None  # Cross-page chunks don't have single page
        
        return chunks


def create_chunker(
    strategy: str = "sentence",
    max_chars: int = 900,
    max_tokens: int = 512,
    overlap: int = 100,
    **kwargs,
) -> ChunkingStrategy:
    """
    Factory function to create a chunker.
    
    Args:
        strategy: "sentence" or "token"
        max_chars: Max characters for sentence chunker
        max_tokens: Max tokens for token chunker
        overlap: Overlap amount
        
    Returns:
        Configured ChunkingStrategy
    """
    if strategy == "sentence":
        return SentenceChunker(
            max_chars=max_chars,
            overlap_chars=overlap,
            **kwargs,
        )
    elif strategy == "token":
        return TokenChunker(
            max_tokens=max_tokens,
            overlap_tokens=overlap,
            **kwargs,
        )
    else:
        raise ValueError(f"Unknown strategy: {strategy}")
