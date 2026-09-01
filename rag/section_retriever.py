"""
Section-level retrieval for hierarchical document search.

Stage 1 of multi-stage retrieval:
- Identifies most relevant document sections
- Uses both keyword matching and semantic similarity
- Returns top 3 sections for chunk-level retrieval
"""

from typing import List, Dict
from collections import Counter
from langchain_huggingface import HuggingFaceEmbeddings
from core.settings import settings


# Lazy-loaded embeddings
_embeddings = None

def _get_embeddings():
    global _embeddings
    if _embeddings is None:
        if not settings.jina_api_key:
            raise RuntimeError("Jina API key is not configured for section retrieval embeddings.")
        _embeddings = OpenAIEmbeddings(
            model=settings.jina_embedding_model,
            api_key=settings.jina_api_key,
            base_url="https://api.jina.ai/v1",
        )
    return _embeddings


def find_best_section(chunks: List[Dict]) -> str:
    """
    Identify which DRHP section appears most in retrieved chunks.
    Used as fallback when section retrieval fails.
    """
    sections = [
        c.get("section", "general")
        for c in chunks
        if c.get("section")
    ]

    if not sections:
        return "general"

    counts = Counter(sections)
    return counts.most_common(1)[0][0]


def retrieve_sections_by_keywords(query: str, sections: List[Dict], top_k: int = 3) -> List[Dict]:
    """
    Score sections by keyword relevance to query.
    
    Args:
        query: User query
        sections: List of section dictionaries with start_page, end_page, section_name
        top_k: Number of sections to return
    
    Returns:
        Top-k sections ranked by keyword relevance
    """
    from collections import Counter
    import re
    
    query_tokens = set(re.findall(r'\b\w+\b', query.lower()))
    
    scored_sections = []
    
    for section in sections:
        section_name = section.get("section_name", "").lower()
        section_tokens = set(re.findall(r'\b\w+\b', section_name))
        
        # Count matching tokens
        matches = len(query_tokens & section_tokens)
        score = matches / max(len(query_tokens), 1)
        
        scored_sections.append((section, score))
    
    # Sort by score descending
    scored_sections.sort(key=lambda x: x[1], reverse=True)
    
    # Return top-k
    result = [s[0] for s in scored_sections[:top_k] if s[1] > 0]
    
    # If no keyword matches, return first sections
    if not result:
        result = sections[:top_k]
    
    print(f"[SECTION_RETRIEVAL] Keyword-based section selection:")
    for s in result[:3]:
        print(f"  - {s['section_name']} (pages {s['start_page']}-{s['end_page']})")
    
    return result


def retrieve_top_sections(
    query: str,
    ipo_id: str,
    sections: List[Dict],
    top_k: int = 3
) -> List[Dict]:
    """
    Retrieve top relevant sections using hybrid approach.
    
    Stage 1 of multi-stage retrieval pipeline:
    1. Keyword-based section scoring
    2. Filter to top-k most relevant sections
    
    Args:
        query: User query
        ipo_id: Document ID
        sections: List of section metadata from TOC
        top_k: Number of sections to return
    
    Returns:
        Top-k relevant sections for chunk-level retrieval
    """
    print(f"\n[SECTION_RETRIEVAL] Starting section-level retrieval")
    print(f"[SECTION_RETRIEVAL] Query: {query[:60]}")
    print(f"[SECTION_RETRIEVAL] Total sections available: {len(sections)}")
    
    if not sections:
        print("[SECTION_RETRIEVAL] No sections provided, returning empty")
        return []
    
    # Use keyword-based scoring (semantic section embeddings not available yet)
    top_sections = retrieve_sections_by_keywords(query, sections, top_k=top_k)
    
    print(f"[SECTION_RETRIEVAL] Selected {len(top_sections)} sections for chunk retrieval")
    
    return top_sections

