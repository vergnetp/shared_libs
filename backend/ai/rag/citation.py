"""
Citation guardrail for hallucination prevention.

Ensures answers contain proper citations and rejects uncited claims.

Usage:
    from rag import CitationGuardrail
    
    guardrail = CitationGuardrail(llm_fn=my_llm)
    
    # Check if answer has proper citations
    result = await guardrail.check(
        answer="The rent is $2000 [Source 1].",
        sources=[{"content": "Rent: $2000", ...}],
    )
    
    if not result.passed:
        # Reject or fix the answer
        fixed = await guardrail.fix(answer, sources)
"""

import re
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, field


@dataclass
class CitationCheckResult:
    """Result of citation check."""
    passed: bool
    cited_claims: List[str] = field(default_factory=list)
    uncited_claims: List[str] = field(default_factory=list)
    invalid_citations: List[str] = field(default_factory=list)
    message: str = ""
    
    @property
    def citation_rate(self) -> float:
        """Percentage of claims that are cited."""
        total = len(self.cited_claims) + len(self.uncited_claims)
        if total == 0:
            return 1.0
        return len(self.cited_claims) / total


CHECK_CITATIONS_PROMPT = """Analyze this answer for proper citations.

Answer:
{answer}

Available sources:
{sources}

For each factual claim in the answer:
1. Check if it has a citation (e.g., [Source 1], [doc1], etc.)
2. Check if the citation correctly matches the source content

Response format (JSON):
{{
  "cited_claims": [
    {{"claim": "The rent is $2000", "citation": "[Source 1]", "valid": true}}
  ],
  "uncited_claims": [
    "Payment is due on the 1st"
  ],
  "invalid_citations": [
    {{"claim": "Lease expires in December", "citation": "[Source 2]", "reason": "Source 2 says January"}}
  ]
}}

Analysis:"""


ADD_CITATIONS_PROMPT = """Add citations to this answer using the provided sources.

Original answer:
{answer}

Sources:
{sources}

Rules:
- Add [Source N] citation after each factual claim
- If a fact is not in any source, replace with "[UNKNOWN]"
- Do not invent information
- Keep the answer structure intact

Cited answer:"""


STRICT_ANSWER_PROMPT = """Answer ONLY using information from the provided sources.

Question: {question}

Sources:
{sources}

Rules:
- ONLY use facts explicitly stated in the sources
- Add [Source N] citation after each fact
- If the answer is not in the sources, say "UNKNOWN - not found in provided sources"
- Do NOT infer, extrapolate, or use prior knowledge
- For dates and numbers: must match exactly

Format:
- Use citations like [Source 1], [Source 2]
- If partially answerable: provide what's known, mark rest as UNKNOWN

Answer:"""


class CitationGuardrail:
    """
    Guardrail ensuring proper citations in answers.
    
    Modes:
    - check: Verify existing citations
    - fix: Add citations to uncited answer
    - strict: Generate new answer with mandatory citations
    
    Usage:
        guardrail = CitationGuardrail(llm_fn=my_llm)
        
        # Check existing answer
        result = await guardrail.check(answer, sources)
        
        if not result.passed:
            # Option 1: Fix the answer
            fixed = await guardrail.fix(answer, sources)
            
            # Option 2: Regenerate strictly
            strict = await guardrail.strict_answer(question, sources)
    """
    
    def __init__(
        self,
        llm_fn: Callable[[List[Dict]], str] = None,
        require_all_cited: bool = True,
        min_citation_rate: float = 0.8,
    ):
        """
        Args:
            llm_fn: LLM function for checking/fixing
            require_all_cited: If True, all claims must be cited
            min_citation_rate: Minimum % of claims that must be cited
        """
        self.llm_fn = llm_fn
        self.require_all_cited = require_all_cited
        self.min_citation_rate = min_citation_rate
    
    async def check(
        self,
        answer: str,
        sources: List[Dict[str, Any]],
        content_key: str = "content",
    ) -> CitationCheckResult:
        """
        Check if answer has proper citations.
        
        Args:
            answer: Answer to check
            sources: Source documents
            content_key: Key for content in sources
            
        Returns:
            CitationCheckResult with check details
        """
        # Quick check: does it have any citations?
        has_citations = bool(re.search(r'\[.*?\]', answer))
        
        if not has_citations:
            # Simple case: no citations at all
            # Check if answer has factual claims
            if self._is_factual(answer):
                return CitationCheckResult(
                    passed=False,
                    uncited_claims=[answer],
                    message="Answer contains factual claims but no citations",
                )
            else:
                # No factual claims (e.g., "I don't know")
                return CitationCheckResult(
                    passed=True,
                    message="No factual claims requiring citation",
                )
        
        # Use LLM for detailed check
        if self.llm_fn is None:
            # Without LLM, just check citation presence
            return CitationCheckResult(
                passed=True,
                message="Citations present (not validated)",
            )
        
        return await self._detailed_check(answer, sources, content_key)
    
    async def _detailed_check(
        self,
        answer: str,
        sources: List[Dict[str, Any]],
        content_key: str,
    ) -> CitationCheckResult:
        """Detailed LLM-based citation check."""
        # Format sources
        source_text = "\n\n".join(
            f"[Source {i+1}]: {s.get(content_key, '')[:1000]}"
            for i, s in enumerate(sources)
        )
        
        prompt = CHECK_CITATIONS_PROMPT.format(
            answer=answer,
            sources=source_text,
        )
        
        response = await self.llm_fn([{"role": "user", "content": prompt}])
        
        # Parse response
        import json
        try:
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if match:
                data = json.loads(match.group())
                
                cited = [c.get("claim", "") for c in data.get("cited_claims", [])]
                uncited = data.get("uncited_claims", [])
                invalid = data.get("invalid_citations", [])
                
                # Determine if passed
                total = len(cited) + len(uncited)
                citation_rate = len(cited) / total if total > 0 else 1.0
                
                if self.require_all_cited:
                    passed = len(uncited) == 0 and len(invalid) == 0
                else:
                    passed = citation_rate >= self.min_citation_rate and len(invalid) == 0
                
                return CitationCheckResult(
                    passed=passed,
                    cited_claims=cited,
                    uncited_claims=uncited,
                    invalid_citations=[str(i) for i in invalid],
                    message=f"Citation rate: {citation_rate:.0%}",
                )
        except (json.JSONDecodeError, AttributeError):
            pass
        
        # Fallback
        return CitationCheckResult(
            passed=True,
            message="Could not parse check result",
        )
    
    def _is_factual(self, text: str) -> bool:
        """Check if text contains factual claims (heuristic)."""
        # Look for patterns that indicate facts
        factual_patterns = [
            r'\$\d+',  # Money
            r'\d{1,2}/\d{1,2}/\d{2,4}',  # Dates
            r'\d+%',  # Percentages
            r'\d+\s*(years?|months?|days?|hours?)',  # Durations
            r'(is|are|was|were)\s+\w+',  # Assertions
        ]
        
        for pattern in factual_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        
        # Check for "unknown" type responses
        unknown_patterns = [
            r"don'?t know",
            r"not sure",
            r"cannot find",
            r"no information",
            r"unknown",
            r"not found",
        ]
        
        for pattern in unknown_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return False
        
        # Default: assume factual if longer than a few words
        return len(text.split()) > 10
    
    async def fix(
        self,
        answer: str,
        sources: List[Dict[str, Any]],
        content_key: str = "content",
    ) -> str:
        """
        Add citations to an uncited answer.
        
        Args:
            answer: Answer without citations
            sources: Source documents
            content_key: Key for content
            
        Returns:
            Answer with citations added
        """
        if self.llm_fn is None:
            raise ValueError("LLM function required for fix")
        
        source_text = "\n\n".join(
            f"[Source {i+1}]: {s.get(content_key, '')[:1000]}"
            for i, s in enumerate(sources)
        )
        
        prompt = ADD_CITATIONS_PROMPT.format(
            answer=answer,
            sources=source_text,
        )
        
        response = await self.llm_fn([{"role": "user", "content": prompt}])
        return response.strip()
    
    async def strict_answer(
        self,
        question: str,
        sources: List[Dict[str, Any]],
        content_key: str = "content",
    ) -> str:
        """
        Generate a new answer with mandatory citations.
        
        Use this when you want to regenerate rather than fix.
        
        Args:
            question: Original question
            sources: Source documents
            content_key: Key for content
            
        Returns:
            Strictly cited answer or UNKNOWN
        """
        if self.llm_fn is None:
            raise ValueError("LLM function required for strict_answer")
        
        source_text = "\n\n".join(
            f"[Source {i+1}]: {s.get(content_key, '')}"
            for i, s in enumerate(sources)
        )
        
        prompt = STRICT_ANSWER_PROMPT.format(
            question=question,
            sources=source_text,
        )
        
        response = await self.llm_fn([{"role": "user", "content": prompt}])
        return response.strip()


class FailClosedGuardrail:
    """
    Fail-closed guardrail that blocks uncited answers.
    
    Unlike CitationGuardrail which can fix, this one blocks.
    
    Usage:
        guardrail = FailClosedGuardrail()
        
        try:
            guardrail.enforce(answer, sources)
        except UncitedAnswerError:
            # Answer blocked
    """
    
    def __init__(
        self,
        require_citation_pattern: str = r'\[.*?\]',
        allow_unknown: bool = True,
    ):
        self.citation_pattern = require_citation_pattern
        self.allow_unknown = allow_unknown
    
    def enforce(
        self,
        answer: str,
        sources: List[Dict[str, Any]] = None,
    ) -> bool:
        """
        Enforce citation requirement.
        
        Returns:
            True if answer passes
            
        Raises:
            UncitedAnswerError if answer lacks citations
        """
        # Check for UNKNOWN responses
        if self.allow_unknown:
            unknown_patterns = ["unknown", "not found", "cannot find", "no information"]
            if any(p in answer.lower() for p in unknown_patterns):
                return True
        
        # Check for citations
        has_citations = bool(re.search(self.citation_pattern, answer))
        
        if not has_citations:
            # Check if it's a factual answer
            if len(answer.split()) > 10 and not answer.strip().endswith('?'):
                raise UncitedAnswerError(
                    "Answer contains factual claims without citations",
                    answer=answer,
                )
        
        return True


class UncitedAnswerError(Exception):
    """Raised when an answer lacks required citations."""
    
    def __init__(self, message: str, answer: str = None):
        super().__init__(message)
        self.answer = answer
