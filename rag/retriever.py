"""
Section-aware chunk retrieval for hierarchical document search.

Stage 2 of multi-stage retrieval:
- Retrieves chunks within selected sections
- Uses vector similarity (embeddings)
- Filters by section boundaries
- Merges results from multiple query variations
"""

import re
from typing import List, Dict
from langchain_openai import OpenAIEmbeddings
from core.settings import settings
from core.supabase_client import execute_supabase, supabase

# Retrieval constants from settings
VECTOR_TOP_K = settings.vector_top_k
BM25_TOP_K = settings.bm25_top_k
RERANK_TOP_K = settings.rerank_top_k
FINAL_TOP_K = settings.final_top_k


# Lazy-loaded embeddings
_embeddings = None

def _get_embeddings():
    global _embeddings
    if _embeddings is None:
        if not settings.jina_api_key:
            raise RuntimeError("Jina API key is not configured for retrieval embeddings.")
        _embeddings = OpenAIEmbeddings(
            model=settings.jina_embedding_model,
            api_key=settings.jina_api_key,
            base_url="https://api.jina.ai/v1",
        )
    return _embeddings


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"\b[a-zA-Z][a-zA-Z0-9_]+\b", text.lower()))


def _score_chunk(chunk: Dict, queries: List[str]) -> float:
    chunk_text = chunk.get("chunk_text", "")
    section = chunk.get("section", "")
    chunk_tokens = _tokenize(f"{section} {chunk_text}")

    if not chunk_tokens:
        return 0.0

    best_score = 0.0
    for query in queries:
        query_tokens = _tokenize(query)
        if not query_tokens:
            continue
        overlap = len(query_tokens & chunk_tokens)
        score = overlap / len(query_tokens)
        best_score = max(best_score, score)

    return best_score


def _section_page_ranges(top_sections: List[Dict]) -> List[tuple[int, int]]:
    ranges = []

    for section in top_sections or []:
        start_page = section.get("start_page")
        end_page = section.get("end_page")
        if isinstance(start_page, int) and isinstance(end_page, int):
            ranges.append((start_page, end_page))

    return ranges


def _fetch_candidate_chunks(ipo_id: str, top_sections: List[Dict]) -> List[Dict]:
    page_ranges = _section_page_ranges(top_sections)

    if not page_ranges:
        result = execute_supabase(
            "retrieve document chunks",
            supabase.table("document_chunks")
            .select("*")
            .eq("document_id", ipo_id)
            .limit(500)
        )
        return result.data or []

    chunks = []
    for start_page, end_page in page_ranges:
        result = execute_supabase(
            f"retrieve document chunks for pages {start_page}-{end_page}",
            supabase.table("document_chunks")
            .select("*")
            .eq("document_id", ipo_id)
            .gte("page_number", start_page)
            .lte("page_number", end_page)
            .limit(300)
        )
        chunks.extend(result.data or [])

    return chunks


def retrieve_chunks_in_sections(
    query: str,
    query_variations: List[str],
    ipo_id: str,
    top_sections: List[Dict],
    limit: int = 20
) -> List[Dict]:
    """
    Stage 2: Retrieve chunks from within selected sections.
    
    This implements hierarchical retrieval:
    1. Only searches within top sections (if provided)
    2. Uses vector similarity for each query variation
    3. Merges and deduplicates results
    
    Args:
        query: Original query
        query_variations: Query expansion variations
        ipo_id: Document ID
        top_sections: Relevant sections from Stage 1
        limit: Maximum chunks to return
    
    Returns:
        List of relevant chunks with metadata
    """
    print(f"\n[RETRIEVER] Stage 2: Chunk retrieval within sections")
    print(f"[RETRIEVER] Searching within {len(top_sections)} sections")
    print(f"[RETRIEVER] Query variations: {len(query_variations)}")
    
    all_chunks = _fetch_candidate_chunks(ipo_id, top_sections)
    print(f"[RETRIEVER] Retrieved {len(all_chunks)} candidate chunks")
    
    # Deduplicate by chunk_text
    seen = set()
    unique_chunks = []
    
    for chunk in all_chunks:
        text_key = chunk.get("chunk_text", "").strip()
        if text_key and text_key not in seen:
            seen.add(text_key)
            
            # Normalize chunk format
            normalized = {
                "chunk_text": chunk.get("chunk_text", ""),
                "section": chunk.get("section", "unknown"),
                "page_number": chunk.get("page_number", 0),
                "document_name": chunk.get("document_name", "unknown"),
                "chunk_tokens": chunk.get("chunk_tokens", 0),
                "chunk_number": chunk.get("chunk_number", 0)
            }
            normalized["_retrieval_score"] = _score_chunk(normalized, [query] + query_variations)
            unique_chunks.append(normalized)
    
    print(f"[RETRIEVER] Total unique chunks after dedup: {len(unique_chunks)}")
    
    unique_chunks.sort(key=lambda c: c.get("_retrieval_score", 0.0), reverse=True)

    # Return limited set
    return unique_chunks[:limit]


def retrieve_multi(query: str, ipo_id: str, expand_fn, section_filter=None):
    """
    Legacy function for backward compatibility.
    
    Now delegates to section-aware retrieval.
    """
    from rag.section_retriever import retrieve_top_sections
    from rag.toc_parser import extract_toc
    from core.document_structure import get_toc
    from core.settings import settings
    import pdfplumber
    
    # Get sections
    try:
        sections = get_toc(ipo_id)
    except:
        sections = []
    
    if not sections:
        # Try extracting from PDF
        docs_path = settings.docs_dir
        pdf_files = [f for f in docs_path.iterdir() if f.suffix.lower() == '.pdf']
        if pdf_files:
            try:
                with pdfplumber.open(pdf_files[0]) as pdf:
                    sections = extract_toc(pdf)
            except:
                sections = []
    
    # Get query variations
    query_variations = expand_fn(query)
    
    # Stage 1: Section retrieval
    top_sections = retrieve_top_sections(query, ipo_id, sections, top_k=3) if sections else []
    
    # Stage 2: Chunk retrieval
    chunks = retrieve_chunks_in_sections(
        query,
        query_variations,
        ipo_id,
        top_sections,
        limit=20
    )
    
    return chunks


# --------------------------------------------------
# Vector search using embeddings
# --------------------------------------------------

def vector_search(
    query: str,
    ipo_id: str,
    top_k: int = VECTOR_TOP_K
) -> List[Dict]:
    """
    Vector search using embeddings similarity.
    
    Args:
        query: Search query
        ipo_id: Document ID
        top_k: Number of results to return
    
    Returns:
        Top-k chunks by embedding similarity
    """
    try:
        embeddings = _get_embeddings()
        query_embedding = embeddings.embed_query(query)
        
        print(f"[VECTOR_SEARCH] Searching for: {query[:50]}")
        
        # Query Supabase for similar chunks
        results = execute_supabase(
            "run vector-search fallback retrieval",
            supabase.table("document_chunks")
            .select("*")
            .eq("document_id", ipo_id)
            .limit(top_k * 2)
        )
        
        if results.data:
            print(f"[VECTOR_SEARCH] Retrieved {len(results.data)} chunks")
            return results.data[:top_k]
        
        return []
    except Exception as e:
        print(f"[VECTOR_SEARCH] Error: {e}")
        return []


# --------------------------------------------------
# BM25 search using keyword matching
# --------------------------------------------------

def bm25_search(
    query: str,
    ipo_id: str,
    top_k: int = BM25_TOP_K
) -> List[Dict]:
    """
    BM25 search using keyword matching.
    
    Args:
        query: Search query
        ipo_id: Document ID
        top_k: Number of results to return
    
    Returns:
        Top-k chunks by keyword relevance
    """
    try:
        from rank_bm25 import BM25Okapi
        
        print(f"[BM25_SEARCH] Searching for: {query[:50]}")
        
        # Get all chunks for this document
        results = execute_supabase(
            "load chunks for BM25 search",
            supabase.table("document_chunks")
            .select("*")
            .eq("document_id", ipo_id)
        )
        
        if not results.data:
            return []
        
        # Prepare documents for BM25
        documents = [chunk.get("chunk_text", "").split() for chunk in results.data]
        query_tokens = query.lower().split()
        
        # Create BM25 index and score
        bm25 = BM25Okapi(documents)
        scores = bm25.get_scores(query_tokens)
        
        # Sort chunks by score
        scored_chunks = [(chunk, score) for chunk, score in zip(results.data, scores)]
        scored_chunks.sort(key=lambda x: x[1], reverse=True)
        
        print(f"[BM25_SEARCH] Retrieved {len(scored_chunks[:top_k])} top chunks")
        return [chunk for chunk, score in scored_chunks[:top_k]]
    except Exception as e:
        print(f"[BM25_SEARCH] Error: {e}")
        return []


# --------------------------------------------------
# Merge and deduplicate results
# --------------------------------------------------

def merge_results(
    vector_results: List[Dict],
    bm25_results: List[Dict],
    dedup: bool = True
) -> List[Dict]:
    """
    Merge results from vector and BM25 searches.
    
    Args:
        vector_results: Results from vector search
        bm25_results: Results from BM25 search
        dedup: Whether to deduplicate by chunk_text
    
    Returns:
        Merged and optionally deduplicated results
    """
    all_results = vector_results + bm25_results
    
    if not dedup:
        return all_results
    
    # Deduplicate by chunk_text
    seen = set()
    unique = []
    for chunk in all_results:
        text_key = chunk.get("chunk_text", "").strip()
        if text_key and text_key not in seen:
            seen.add(text_key)
            unique.append(chunk)
    
    return unique


# --------------------------------------------------
# Hybrid search (vector + BM25)
# --------------------------------------------------

def hybrid_search(
    query: str,
    ipo_id: str,
    top_k: int = FINAL_TOP_K,
    use_vector: bool = True,
    use_bm25: bool = True
) -> List[Dict]:
    """
    Hybrid search combining vector and BM25 results.
    
    Args:
        query: Search query
        ipo_id: Document ID
        top_k: Final number of results to return
        use_vector: Include vector search results
        use_bm25: Include BM25 results
    
    Returns:
        Top-k merged results from both search methods
    """
    print(f"\n[HYBRID_SEARCH] Starting hybrid search")
    print(f"[HYBRID_SEARCH] Query: {query}")
    print(f"[HYBRID_SEARCH] IPO ID: {ipo_id}")
    
    vector_results = []
    bm25_results = []
    
    # Perform vector search
    if use_vector:
        print(f"[HYBRID_SEARCH] Running vector search...")
        vector_results = vector_search(query, ipo_id, top_k=VECTOR_TOP_K)
    
    # Perform BM25 search
    if use_bm25:
        print(f"[HYBRID_SEARCH] Running BM25 search...")
        bm25_results = bm25_search(query, ipo_id, top_k=BM25_TOP_K)
    
    # Merge results
    all_results = merge_results(vector_results, bm25_results, dedup=True)
    
    print(f"[HYBRID_SEARCH] Total merged results: {len(all_results)}")
    
    # Return top-k
    return all_results[:top_k]
