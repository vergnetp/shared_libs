"""
Token counting utilities - shared across all AI modules.

Usage:
    from ai.tokens import estimate_tokens, count_tokens
    
    # Fast heuristic (no deps)
    tokens = estimate_tokens("Hello world")
    
    # Accurate (requires tiktoken)
    tokens = count_tokens("Hello world", model="gpt-4")
"""

from typing import Callable, Optional


def estimate_tokens(text: str) -> int:
    """
    Heuristic token estimation (CJK-aware).
    
    Fast, no dependencies. ~90% accurate for English, ~80% for CJK.
    
    Args:
        text: Text to estimate
        
    Returns:
        Estimated token count
    """
    if not text:
        return 0
    
    cjk_count = 0
    for c in text:
        cp = ord(c)
        if (0x4E00 <= cp <= 0x9FFF or    # CJK Unified Ideographs
            0x3400 <= cp <= 0x4DBF or    # CJK Extension A
            0x3040 <= cp <= 0x30FF or    # Hiragana + Katakana
            0xAC00 <= cp <= 0xD7AF or    # Korean Hangul
            0x0600 <= cp <= 0x06FF or    # Arabic
            0x0590 <= cp <= 0x05FF):     # Hebrew
            cjk_count += 1
    
    latin_count = len(text) - cjk_count
    cjk_tokens = cjk_count * 0.7
    latin_tokens = latin_count / 3.5
    
    return max(1, int(cjk_tokens + latin_tokens))


def count_tokens(text: str, model: str = "gpt-4") -> int:
    """
    Accurate token count using tiktoken.
    
    Falls back to estimate_tokens if tiktoken not available.
    
    Args:
        text: Text to count
        model: Model name (gpt-4, claude, etc.)
        
    Returns:
        Token count
    """
    if not text:
        return 0
    
    try:
        import tiktoken
        
        # Most models use cl100k_base
        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")
        
        return len(encoding.encode(text))
        
    except ImportError:
        return estimate_tokens(text)


def truncate_to_tokens(text: str, max_tokens: int, model: str = None) -> str:
    """
    Truncate text to fit within token limit.
    
    Args:
        text: Text to truncate
        max_tokens: Maximum tokens
        model: Model for accurate counting (None = heuristic)
        
    Returns:
        Truncated text
    """
    if not text:
        return text
    
    count_fn = (lambda t: count_tokens(t, model)) if model else estimate_tokens
    
    if count_fn(text) <= max_tokens:
        return text
    
    # Binary search on words
    words = text.split()
    low, high = 0, len(words)
    
    while low < high:
        mid = (low + high + 1) // 2
        candidate = " ".join(words[:mid])
        if count_fn(candidate) <= max_tokens:
            low = mid
        else:
            high = mid - 1
    
    return " ".join(words[:low])


def create_counter(model: str = None) -> Callable[[str], int]:
    """
    Create a token counter function.
    
    Args:
        model: Model name (None = heuristic)
        
    Returns:
        Token counting function
    """
    if model:
        return lambda text: count_tokens(text, model)
    return estimate_tokens
