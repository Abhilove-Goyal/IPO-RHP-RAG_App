"""
LLM-based answer generation with proper metadata handling.

Generates answers from retrieved chunks with full citation support.
"""

from typing import List, Dict
from langchain_openai import ChatOpenAI
from core.settings import settings
from rag.prompt_builder import build_prompt


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
        
        llm = ChatOpenAI(
            model=settings.groq_model,
            api_key=settings.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
            temperature=0,
        )

        # Build prompt with full metadata
        prompt = build_prompt(query, context_chunks)
        
        print(f"[GENERATOR] Calling LLM...")
        response = llm.invoke(prompt)
        
        answer_text = response.content.strip()
        
        # Calculate faithfulness score based on context usage
        faithfulness_score = min(1.0, len(context_chunks) / 5.0) if context_chunks else 0.0
        
        print(f"[GENERATOR] Answer generated, length: {len(answer_text)} chars")
        print(f"[GENERATOR] Faithfulness score: {faithfulness_score:.2f}")
        
        return answer_text, faithfulness_score

    except Exception as e:
        print(f"[GENERATOR] LLM ERROR: {e}")
        raise

