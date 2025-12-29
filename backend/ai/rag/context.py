"""Context preparation for LLM consumption."""

from typing import List, Dict, Any, Optional, Callable


def estimate_tokens(text: str) -> int:
    """
    Heuristic token estimation (imported from embeddings.tokenizer).
    
    For accurate counting, use:
        from embeddings import count_tokens
        count = count_tokens(text, model="gpt-4")
    """
    if not text:
        return 0
    
    # Count CJK characters (Chinese, Japanese, Korean)
    cjk_count = 0
    for c in text:
        cp = ord(c)
        if (0x4E00 <= cp <= 0x9FFF or    # CJK Unified Ideographs
            0x3400 <= cp <= 0x4DBF or    # CJK Extension A
            0x3040 <= cp <= 0x30FF or    # Hiragana + Katakana
            0xAC00 <= cp <= 0xD7AF):     # Korean Hangul
            cjk_count += 1
    
    latin_count = len(text) - cjk_count
    cjk_tokens = cjk_count * 0.7
    latin_tokens = latin_count / 3.5
    
    return max(1, int(cjk_tokens + latin_tokens))


def create_token_counter(llm_model: str = None) -> Callable[[str], int]:
    """
    Create a token counter function for a specific LLM.
    
    Args:
        llm_model: LLM model name (e.g., "gpt-4", "claude-sonnet")
                   If None, returns heuristic counter
    
    Returns:
        Token counting function
    
    Usage:
        count_fn = create_token_counter("gpt-4")
        tokens = count_fn("Hello world")
    """
    if llm_model is None:
        return estimate_tokens
    
    try:
        from embeddings import count_tokens
        return lambda text: count_tokens(text, model=llm_model)
    except ImportError:
        return estimate_tokens


def trim_to_tokens(
    text: str,
    max_tokens: int,
    count_fn = None,
) -> str:
    """
    Trim text to approximately max_tokens.
    
    Args:
        text: Text to trim
        max_tokens: Maximum token count
        count_fn: Function to count tokens (default: estimate)
        
    Returns:
        Trimmed text
    """
    if count_fn is None:
        count_fn = estimate_tokens
    
    if count_fn(text) <= max_tokens:
        return text
    
    # Binary search for cutoff
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


def format_context(
    documents: List[Dict[str, Any]],
    content_key: str = "content",
    max_tokens: int = 3000,
    include_metadata: bool = True,
    count_fn = None,
) -> str:
    """
    Format documents into context string for LLM.
    
    Args:
        documents: List of document dicts
        content_key: Key for text content
        max_tokens: Maximum total tokens
        include_metadata: Whether to include source info
        count_fn: Token counting function (default: estimate)
        
    Returns:
        Formatted context string
    """
    if not documents:
        return ""
    
    if count_fn is None:
        count_fn = estimate_tokens
    
    parts = []
    current_tokens = 0
    
    for i, doc in enumerate(documents, 1):
        content = doc.get(content_key, "")
        
        # Format with metadata
        if include_metadata:
            metadata = doc.get("metadata", {})
            source = metadata.get("filename", metadata.get("source", f"Source {i}"))
            page = metadata.get("page_num", "")
            
            if page:
                header = f"[{source}, p.{page}]"
            else:
                header = f"[{source}]"
            
            chunk_text = f"{header}\n{content}"
        else:
            chunk_text = content
        
        chunk_tokens = count_fn(chunk_text)
        
        # Check if we'd exceed limit
        if current_tokens + chunk_tokens > max_tokens:
            # Trim this chunk to fit
            remaining = max_tokens - current_tokens
            if remaining > 100:  # Only add if meaningful space left
                trimmed = trim_to_tokens(chunk_text, remaining, count_fn)
                parts.append(trimmed)
            break
        
        parts.append(chunk_text)
        current_tokens += chunk_tokens
    
    return "\n\n---\n\n".join(parts)


def build_rag_prompt(
    question: str,
    context: str,
    system_prompt: str = None,
) -> List[Dict[str, str]]:
    """
    Build messages for RAG query.
    
    Args:
        question: User's question
        context: Retrieved context
        system_prompt: Optional custom system prompt
        
    Returns:
        List of message dicts for LLM
    """
    if system_prompt is None:
        system_prompt = RAG_PROMPT_FORBIDDEN  # Default: no assumptions allowed
    
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": f"""Context:
{context}

---

Question: {question}"""
        },
    ]
    
    return messages


# ============================================================================
# PROMPTS FOR assumptions="forbidden" (default - no hallucinations)
# ============================================================================

RAG_PROMPT_FORBIDDEN = """You are a helpful assistant that answers questions based on the provided context.

CRITICAL RULES:
- Answer using ONLY information explicitly stated in the context below
- If information is NOT in the context, say "I don't know" or "I couldn't find this information"
- Do NOT guess, infer, or extrapolate beyond what's stated
- Dates and numbers must match the source exactly
- Add [Source N] citation after each factual claim
- If you can partially answer, provide what's known and clearly state what you couldn't find

Format:
- Cite facts: "The rent is $2000 [Source 1]"
- Unknown facts: "I couldn't find information about the security deposit in the provided documents"
- Respond in the same language as the question"""


ANALYTICAL_PROMPT_FORBIDDEN = """You are an analytical assistant with strict sourcing requirements.

For each claim in your response:
- If from sources: cite with [Source N]
- If derived (math only): show calculation
- If not in sources: say "I don't know" or "Not found in sources"

Contract:
- No unstated assumptions
- No inferred attributes  
- No guessed dates/numbers
- When uncertain, say "I don't know" rather than guess

Respond in the same language as the question."""


# ============================================================================
# PROMPTS FOR assumptions="allowed" (creative/casual use)
# ============================================================================

RAG_PROMPT_ALLOWED = """You are a helpful assistant that answers questions using the provided context.

Guidelines:
- Use the context to inform your answer
- You may draw on general knowledge to supplement the context
- Cite sources when directly quoting: [Source N]
- Be helpful and informative

Respond in the same language as the question."""


# Legacy aliases
STRICT_RAG_PROMPT = RAG_PROMPT_FORBIDDEN
ANALYTICAL_RAG_PROMPT = ANALYTICAL_PROMPT_FORBIDDEN


class ContextBuilder:
    """
    Build context for RAG queries.
    
    Usage:
        builder = ContextBuilder(
            max_tokens=3000,
            count_fn=count_tokens,  # from embeddings
        )
        
        context = builder.build(documents)
        prompt = builder.build_prompt(question, documents)
    """
    
    def __init__(
        self,
        max_tokens: int = 3000,
        count_fn = None,
        include_metadata: bool = True,
        system_prompt: str = None,
    ):
        self.max_tokens = max_tokens
        self.count_fn = count_fn or (lambda t: len(t) // 4)
        self.include_metadata = include_metadata
        self.system_prompt = system_prompt
    
    def build(
        self,
        documents: List[Dict[str, Any]],
        content_key: str = "content",
    ) -> str:
        """Build context string from documents."""
        return format_context(
            documents=documents,
            content_key=content_key,
            max_tokens=self.max_tokens,
            include_metadata=self.include_metadata,
            count_fn=self.count_fn,
        )
    
    def build_prompt(
        self,
        question: str,
        documents: List[Dict[str, Any]],
        content_key: str = "content",
    ) -> List[Dict[str, str]]:
        """Build full RAG prompt with context."""
        context = self.build(documents, content_key)
        return build_rag_prompt(question, context, self.system_prompt)
