"""
Verification module for hallucination prevention.

Two parameters control hallucination behavior:

1. assumptions: "allowed" | "forbidden" (prompt-level, free)
   - "forbidden": Prompt says "say I don't know if unsure"
   - "allowed": Normal prompts, LLM can guess

2. verification: None | "batch" | "detailed" (post-check, costs extra calls)
   - None: No verification
   - "batch": Single call checks all claims (+1 LLM call)
   - "detailed": Each claim verified individually (+3 LLM calls)

Usage:
    from rag import Verifier, VerificationMode
    
    # Batch verification (balanced)
    verifier = Verifier(llm_fn=my_llm, mode="batch")
    
    # Detailed verification (thorough)
    verifier = Verifier(llm_fn=my_llm, mode="detailed")
    
    result = await verifier.verify(draft, sources)
"""

import json
import re
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum


class VerificationMode(Enum):
    """Verification thoroughness vs speed tradeoff."""
    NONE = None            # No verification
    BATCH = "batch"        # 1 LLM call - all claims at once
    DETAILED = "detailed"  # 3 LLM calls - each claim individually


@dataclass
class Claim:
    """A single claim extracted from an answer."""
    text: str
    supported: bool = False
    source_quote: str = None
    source_id: str = None
    confidence: float = 0.0


@dataclass
class VerifiedAnswer:
    """Result of verification pass."""
    original_draft: str
    verified_answer: str
    claims: List[Claim] = field(default_factory=list)
    unsupported: List[str] = field(default_factory=list)
    confidence: float = 0.0
    
    @property
    def is_reliable(self) -> bool:
        """True if all claims are supported."""
        return len(self.unsupported) == 0 and self.confidence > 0.7
    
    @property
    def supported_count(self) -> int:
        return sum(1 for c in self.claims if c.supported)
    
    @property
    def total_claims(self) -> int:
        return len(self.claims)


EXTRACT_CLAIMS_PROMPT = """Extract all factual claims from this answer. Return JSON array.

A claim is any statement that asserts a fact (dates, numbers, names, events, attributes).
Do NOT include opinions, hedged statements, or meta-commentary.

Answer to analyze:
{answer}

Return JSON array of claims:
[
  "The rent is $2000 per month",
  "Payment is due on the 1st",
  "The lease was signed on January 15, 2024"
]

Claims (JSON only):"""


VERIFY_CLAIM_PROMPT = """Verify if this claim is supported by the source text.

Claim: {claim}

Source text:
{source_text}

Rules:
- The claim must be EXPLICITLY stated or directly derivable (math only)
- Paraphrasing is OK if meaning is identical
- Dates/numbers must match exactly
- If not explicitly supported, answer NOT_SUPPORTED

Response format (JSON):
{{
  "supported": true/false,
  "quote": "exact quote from source that supports this" or null,
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation"
}}

Verification:"""


# Combined prompt for BATCH mode - single LLM call
BATCH_VERIFY_PROMPT = """Verify this answer against the sources. Do everything in ONE response.

ANSWER TO VERIFY:
{answer}

SOURCES:
{sources}

TASK:
1. Extract each factual claim from the answer
2. Check if it's supported by the sources
3. Provide a corrected answer

Response format (JSON):
{{
  "claims": [
    {{"text": "The rent is $2000", "supported": true, "quote": "Monthly rent: $2000"}},
    {{"text": "Due on the 1st", "supported": false, "quote": null}}
  ],
  "corrected_answer": "The rent is $2000 [Source 1]. The due date is UNKNOWN.",
  "confidence": 0.5
}}

Verification:"""


REWRITE_WITH_UNKNOWNS_PROMPT = """Rewrite this answer, replacing unsupported claims with UNKNOWN.

Original answer:
{original}

Supported claims (keep these):
{supported}

Unsupported claims (replace with UNKNOWN):
{unsupported}

Rules:
- Keep all supported information intact
- Replace unsupported facts with "[UNKNOWN]" or remove the sentence
- Do not invent new information
- Keep the answer coherent and helpful

Rewritten answer:"""


class Verifier:
    """
    Verification for hallucination prevention.
    
    Modes:
    - BATCH: 1 LLM call (all claims in one pass) - Balanced
    - DETAILED: 3 LLM calls (each claim separately) - Thorough
    
    Usage:
        # Batch verification (recommended)
        verifier = Verifier(llm_fn=my_llm, mode="batch")
        
        # Detailed verification (thorough)
        verifier = Verifier(llm_fn=my_llm, mode="detailed")
        
        result = await verifier.verify(
            draft="The rent is $2000/month.",
            sources=[{"content": "Rent: $2000", "id": "doc1"}],
        )
        
        if result.is_reliable:
            return result.verified_answer
    
    Performance:
        BATCH:    +1-2 sec (1 LLM call)
        DETAILED: +3-5 sec (3 LLM calls)
    """
    
    def __init__(
        self,
        llm_fn: Callable[[List[Dict]], str],
        mode: str = "batch",
        min_confidence: float = 0.7,
    ):
        """
        Args:
            llm_fn: Function to call LLM (takes messages, returns string)
            mode: "batch" or "detailed"
            min_confidence: Minimum confidence to consider claim supported
        """
        self.llm_fn = llm_fn
        self.mode = mode
        self.min_confidence = min_confidence
    
    async def verify(
        self,
        draft: str,
        sources: List[Dict[str, Any]],
        content_key: str = "content",
    ) -> VerifiedAnswer:
        """
        Verify a draft answer against sources.
        
        Args:
            draft: Draft answer to verify
            sources: Source documents with content
            content_key: Key for text content in sources
            
        Returns:
            VerifiedAnswer with verification results
        """
        # BATCH mode - single LLM call
        if self.mode == "batch":
            return await self._batch_verify(draft, sources, content_key)
        
        # DETAILED mode - full multi-pass verification
        return await self._detailed_verify(draft, sources, content_key)
    
    async def _batch_verify(
        self,
        draft: str,
        sources: List[Dict[str, Any]],
        content_key: str,
    ) -> VerifiedAnswer:
        """Single-call verification (batch mode)."""
        source_text = "\n\n".join(
            f"[Source {i+1}]: {s.get(content_key, '')[:1500]}"
            for i, s in enumerate(sources)
        )
        
        prompt = BATCH_VERIFY_PROMPT.format(
            answer=draft,
            sources=source_text,
        )
        
        response = await self.llm_fn([{"role": "user", "content": prompt}])
        
        # Parse response
        try:
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if match:
                data = json.loads(match.group())
                
                claims = []
                for c in data.get("claims", []):
                    claims.append(Claim(
                        text=c.get("text", ""),
                        supported=c.get("supported", False),
                        source_quote=c.get("quote"),
                        confidence=0.9 if c.get("supported") else 0.0,
                    ))
                
                unsupported = [c.text for c in claims if not c.supported]
                
                return VerifiedAnswer(
                    original_draft=draft,
                    verified_answer=data.get("corrected_answer", draft),
                    claims=claims,
                    unsupported=unsupported,
                    confidence=data.get("confidence", 0.5),
                )
        except (json.JSONDecodeError, AttributeError):
            pass
        
        # Fallback - couldn't parse, return original
        return VerifiedAnswer(
            original_draft=draft,
            verified_answer=draft,
            confidence=0.5,
        )
    
    async def _detailed_verify(
        self,
        draft: str,
        sources: List[Dict[str, Any]],
        content_key: str,
    ) -> VerifiedAnswer:
        """Full multi-pass verification (detailed mode)."""
        # Step 1: Extract claims
        claims_text = await self._extract_claims(draft)
        claim_strings = self._parse_claims(claims_text)
        
        if not claim_strings:
            # No claims to verify - pass through
            return VerifiedAnswer(
                original_draft=draft,
                verified_answer=draft,
                confidence=1.0,
            )
        
        # Step 2: Verify each claim
        claims = []
        for claim_text in claim_strings:
            claim = await self._verify_claim(claim_text, sources, content_key)
            claims.append(claim)
        
        # Step 3: Collect results
        supported = [c for c in claims if c.supported]
        unsupported = [c.text for c in claims if not c.supported]
        
        # Calculate confidence
        if claims:
            confidence = len(supported) / len(claims)
        else:
            confidence = 1.0
        
        # Step 4: Rewrite if needed (always graceful - never error)
        if unsupported:
            verified_answer = await self._rewrite_with_unknowns(
                draft, 
                [c.text for c in supported],
                unsupported,
            )
        else:
            verified_answer = draft
        
        return VerifiedAnswer(
            original_draft=draft,
            verified_answer=verified_answer,
            claims=claims,
            unsupported=unsupported,
            confidence=confidence,
        )
    
    async def _extract_claims(self, answer: str) -> str:
        """Extract claims from answer."""
        prompt = EXTRACT_CLAIMS_PROMPT.format(answer=answer)
        response = await self.llm_fn([{"role": "user", "content": prompt}])
        return response
    
    def _parse_claims(self, claims_text: str) -> List[str]:
        """Parse claims from LLM response."""
        # Clean up response
        text = claims_text.strip()
        
        # Try to find JSON array
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        
        # Fallback: split by newlines
        lines = [l.strip().strip('-•*').strip() for l in text.split('\n')]
        return [l for l in lines if l and len(l) > 5]
    
    async def _verify_claim(
        self,
        claim: str,
        sources: List[Dict[str, Any]],
        content_key: str,
    ) -> Claim:
        """Verify a single claim against sources."""
        # Combine source texts
        source_text = "\n\n---\n\n".join(
            f"[Source {i+1}]: {s.get(content_key, '')}"
            for i, s in enumerate(sources)
        )
        
        prompt = VERIFY_CLAIM_PROMPT.format(
            claim=claim,
            source_text=source_text[:8000],  # Limit length
        )
        
        response = await self.llm_fn([{"role": "user", "content": prompt}])
        
        # Parse response
        try:
            # Find JSON in response
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if match:
                data = json.loads(match.group())
                return Claim(
                    text=claim,
                    supported=data.get("supported", False),
                    source_quote=data.get("quote"),
                    confidence=data.get("confidence", 0.0),
                )
        except (json.JSONDecodeError, AttributeError):
            pass
        
        # Fallback: check for keywords
        response_lower = response.lower()
        supported = "supported" in response_lower and "not_supported" not in response_lower
        
        return Claim(
            text=claim,
            supported=supported,
            confidence=0.5 if supported else 0.0,
        )
    
    async def _rewrite_with_unknowns(
        self,
        original: str,
        supported: List[str],
        unsupported: List[str],
    ) -> str:
        """Rewrite answer replacing unsupported claims."""
        prompt = REWRITE_WITH_UNKNOWNS_PROMPT.format(
            original=original,
            supported="\n".join(f"- {s}" for s in supported) or "(none)",
            unsupported="\n".join(f"- {u}" for u in unsupported),
        )
        
        response = await self.llm_fn([{"role": "user", "content": prompt}])
        return response.strip()


class QuickVerifier:
    """
    Lightweight verification without full claim extraction.
    
    Checks if answer contains information not in sources.
    Faster but less precise than full Verifier.
    """
    
    QUICK_CHECK_PROMPT = """Check if this answer is fully supported by the sources.

Answer:
{answer}

Sources:
{sources}

Rules:
- Every fact in the answer must be explicitly stated in sources
- Dates and numbers must match exactly
- If ANY unsupported claims exist, list them

Response format (JSON):
{{
  "fully_supported": true/false,
  "unsupported_claims": ["claim 1", "claim 2"] or [],
  "confidence": 0.0-1.0
}}

Check:"""
    
    def __init__(self, llm_fn: Callable[[List[Dict]], str]):
        self.llm_fn = llm_fn
    
    async def check(
        self,
        answer: str,
        sources: List[Dict[str, Any]],
        content_key: str = "content",
    ) -> Dict[str, Any]:
        """
        Quick check if answer is supported.
        
        Returns:
            {"supported": bool, "unsupported": List[str], "confidence": float}
        """
        source_text = "\n\n".join(
            s.get(content_key, "") for s in sources
        )[:6000]
        
        prompt = self.QUICK_CHECK_PROMPT.format(
            answer=answer,
            sources=source_text,
        )
        
        response = await self.llm_fn([{"role": "user", "content": prompt}])
        
        try:
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if match:
                data = json.loads(match.group())
                return {
                    "supported": data.get("fully_supported", False),
                    "unsupported": data.get("unsupported_claims", []),
                    "confidence": data.get("confidence", 0.0),
                }
        except (json.JSONDecodeError, AttributeError):
            pass
        
        # Fallback
        return {
            "supported": "fully_supported\": true" in response.lower(),
            "unsupported": [],
            "confidence": 0.5,
        }
