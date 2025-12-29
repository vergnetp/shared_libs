"""
TinyRoberta Provider - Extractive QA as LLM provider.

Uses tinyroberta-squad2 for extractive question answering.
No hallucination - extracts answer directly from context.

Usage:
    from ai_agents.providers import TinyRobertaProvider
    
    provider = TinyRobertaProvider()
    
    # Context in system prompt, question in user message
    response = await provider.run([
        {"role": "system", "content": "The rent is $2000 per month."},
        {"role": "user", "content": "What is the rent?"},
    ])
    
    print(response.content)  # "$2000 per month"
"""

from typing import List, AsyncIterator

from ..core import ProviderResponse


class TinyRobertaProvider:
    """
    Extractive QA provider using tinyroberta-squad2.
    
    Same interface as other LLM providers (Ollama, Anthropic, etc.)
    but uses extractive QA instead of generation.
    
    Pros:
    - No hallucination (extracts from text)
    - Fast (~50ms)
    - Fully offline
    - No API costs
    
    Cons:
    - Can only extract what's literally in text
    - No synthesis or reasoning
    - 512 token context limit
    """
    
    name = "tinyroberta"
    
    def __init__(self, **kwargs):
        """Initialize provider. Model loaded lazily."""
        self._pipeline = None
    
    def _get_pipeline(self):
        """Lazy load QA pipeline."""
        if self._pipeline is None:
            try:
                from transformers import pipeline
                self._pipeline = pipeline(
                    "question-answering",
                    model="deepset/tinyroberta-squad2",
                )
            except ImportError:
                raise ImportError("Install transformers: pip install transformers")
        return self._pipeline
    
    async def run(
        self,
        messages: List[dict],
        temperature: float = 0.0,
        max_tokens: int = 512,
        tools: List[dict] = None,
        **kwargs,
    ) -> ProviderResponse:
        """
        Extract answer from context.
        
        Expected format:
        - System message: context to extract from
        - Last user message: the question
        """
        # Extract context and question
        context = ""
        question = ""
        
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            
            if role == "system":
                context = content
            elif role == "user":
                question = content
        
        if not context:
            # No system message - use all non-question content
            context = "\n".join(
                msg.get("content", "")
                for msg in messages[:-1]
                if msg.get("role") != "user"
            )
        
        if not context or not question:
            return ProviderResponse(
                content="I couldn't find relevant information to answer this question.",
                usage={"input": 0, "output": 0},
                model="tinyroberta-squad2",
                provider=self.name,
            )
        
        # Run extractive QA
        pipeline = self._get_pipeline()
        
        try:
            result = pipeline(question=question, context=context)
            
            answer = result.get("answer", "").strip()
            score = result.get("score", 0.0)
            
            if score < 0.1:
                answer = "I couldn't find a clear answer in the provided context."
            
            return ProviderResponse(
                content=answer,
                usage={"input": 0, "output": 0},
                model="tinyroberta-squad2",
                provider=self.name,
                raw={"score": score, "start": result.get("start"), "end": result.get("end")},
            )
            
        except Exception as e:
            return ProviderResponse(
                content=f"Error: {str(e)}",
                usage={"input": 0, "output": 0},
                model="tinyroberta-squad2",
                provider=self.name,
            )
    
    async def stream(
        self,
        messages: List[dict],
        **kwargs,
    ) -> AsyncIterator[str]:
        """Stream not meaningful for extractive QA."""
        response = await self.run(messages, **kwargs)
        yield response.content
    
    def count_tokens(self, messages: List[dict]) -> int:
        """Estimate tokens."""
        try:
            from ...tokens import estimate_tokens
        except ImportError:
            from ..memory.token_window import estimate_tokens
        return sum(estimate_tokens(m.get("content", "")) for m in messages)
    
    @property
    def max_context_tokens(self) -> int:
        """Max context for tinyroberta (BERT-based)."""
        return 512
