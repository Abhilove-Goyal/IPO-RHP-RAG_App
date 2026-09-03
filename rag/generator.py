"""
LLM-based answer generation with proper metadata handling.

Generates answers from retrieved chunks with full citation support.
"""

from typing import List, Dict
from core.settings import settings
from rag.model_routing import invoke_with_fallback
from rag.prompt_builder import build_prompt


def clean_answer(text: str) -> str:
    """Remove only control characters that cannot carry answer meaning."""
    if not isinstance(text, str):
        return text
    cleaned = "".join(character for character in text if character in "\n\r\t" or ord(character) >= 32)
    return cleaned if cleaned.strip() else text


def generate_answer(query: str, context_chunks: List[Dict]) -> tuple[str, float]:
    """
    Generate answer from retrieved chunks using LLM.
    
    Args:
        query: User question
        context_chunks: List of chunk dictionaries with metadata:
            - chunk_text: Actual text
            - section: Section name
            - page_number: Page in document
            - document_name: Source document
    
    Returns:
        Tuple of (answer_text, faithfulness_score)
    """
    try:
        if not settings.groq_api_key:
            raise RuntimeError("Groq API key is not configured in settings.")

        print(f"\n[GENERATOR] Starting answer generation")
        print(f"[GENERATOR] Query: {query[:60]}")
        print(f"[GENERATOR] Context chunks: {len(context_chunks)}")
        
        # Build prompt with full metadata
        prompt = build_prompt(query, context_chunks)
        
        print(f"[GENERATOR] Calling LLM...")
        result = invoke_with_fallback(prompt)
        if result.status != "SUCCESS":
            raise result.error or RuntimeError("All configured models failed")

        answer_text = clean_answer(result.text)
        
        # Calculate faithfulness score based on context usage
        faithfulness_score = min(1.0, len(context_chunks) / 5.0) if context_chunks else 0.0
        
        print(f"[GENERATOR] Answer generated, length: {len(answer_text)} chars")
        print(f"[GENERATOR] Faithfulness score: {faithfulness_score:.2f}")
        
        return answer_text, faithfulness_score

    except Exception as e:
        print(f"[GENERATOR] LLM ERROR: {e}")
        raise

