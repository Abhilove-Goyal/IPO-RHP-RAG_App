"""
Production-grade RAG pipeline with hybrid retrieval.

Pipeline flow:
    User Query
        ↓
    Query Expansion (3 variations)
        ↓
    Hybrid Retrieval (Vector + BM25)
        ↓
    Section-based Filtering
        ↓
    Reranking (Cross-Encoder)
        ↓
    Final Context Chunks (top 5)
"""

from typing import List, Dict, Optional
from langchain_openai import OpenAIEmbeddings
from core.settings import settings
from core.supabase_client import retrieve_chunks
from rag.multi_query import generate_query_variations
from rag.retriever import hybrid_search
from rag.reranker import rerank
from rag.section_retriever import find_best_section


# --------------------------------------------------
# Pipeline configuration
# --------------------------------------------------

VECTOR_SEARCH_K = 20  # Initial vector search results
BM25_SEARCH_K = 20    # Initial BM25 results
RERANK_TOP_K = 5      # Final reranked results


# --------------------------------------------------
# Embedding model (lazy loaded)
# --------------------------------------------------

_embeddings = None

def _get_embeddings():
    global _embeddings
    if _embeddings is None:
        if not settings.jina_api_key:
            raise RuntimeError("Jina API key is not configured for RAG pipeline embeddings.")
        _embeddings = OpenAIEmbeddings(
            model=settings.jina_embedding_model,
            api_key=settings.jina_api_key,
            base_url="https://api.jina.ai/v1",
        )
    return _embeddings


# --------------------------------------------------
# Normalize chunks to consistent format
# --------------------------------------------------

def normalize_chunks(chunks: List) -> List[Dict]:
    """
    Ensure all chunks have required metadata fields.
    
    Returns:
        List of normalized chunk dictionaries with:
        - chunk_text: the actual text
        - page_number: page where chunk originated
        - section: document section name
        - document_name: name of source document
    """
    normalized = []
    
    for c in chunks:
        if isinstance(c, str):
            normalized.append({
                "chunk_text": c,
                "page_number": "unknown",
                "section": "unknown",
                "document_name": "unknown"
            })
        elif isinstance(c, dict):
            normalized.append({
                "chunk_text": c.get("chunk_text", ""),
                "page_number": c.get("page_number", "unknown"),
                "section": c.get("section", "unknown"),
                "document_name": c.get("document_name", "unknown")
            })
        else:
            normalized.append({
                "chunk_text": str(c),
                "page_number": "unknown",
                "section": "unknown",
                "document_name": "unknown"
            })
    
    return normalized


# --------------------------------------------------
# Remove duplicate chunks
# --------------------------------------------------

def deduplicate_chunks(chunks: List[Dict]) -> List[Dict]:
    """Remove duplicate chunks based on text similarity."""
    seen_texts = set()
    unique = []
    
    for chunk in chunks:
        text = chunk.get("chunk_text", "").strip()
        if text and text not in seen_texts:
            seen_texts.add(text)
            unique.append(chunk)
    
    return unique


# --------------------------------------------------
# Main RAG Pipeline
# --------------------------------------------------

class RAGPipeline:
    """
    Production RAG pipeline combining:
    - Query expansion
    - Hybrid retrieval (vector + BM25)
    - Section filtering
    - Cross-encoder reranking
    """
    
    def __init__(self):
        self.embeddings = _get_embeddings()
    
    def run(
        self,
        query: str,
        ipo_id: str,
        section_filter: Optional[str] = None,
        top_k: int = RERANK_TOP_K
    ) -> List[Dict]:
        """
        Execute complete RAG pipeline.
        
        Args:
            query: User question
            ipo_id: Document ID in Supabase
            section_filter: Optional section to filter by
            top_k: Number of final results
        
        Returns:
            List of top_k reranked chunks with metadata
        """
        print(f"\n[RAG_PIPELINE] Starting pipeline for query: {query[:50]}...")
        
        # --------------------------------
        # Step 1: Query Expansion
        # --------------------------------
        print("[RAG_PIPELINE] Step 1: Expanding query...")
        expanded_queries = generate_query_variations(query)
        print(f"[RAG_PIPELINE] Generated {len(expanded_queries)} query variations")
        for q in expanded_queries:
            print(f"  - {q[:60]}")
        
        # --------------------------------
        # Step 2: Hybrid Retrieval
        # --------------------------------
        print("[RAG_PIPELINE] Step 2: Hybrid retrieval (Vector + BM25)...")
        candidate_chunks = self._hybrid_retrieval(
            expanded_queries,
            ipo_id,
            k=VECTOR_SEARCH_K
        )
        
        if not candidate_chunks:
            print("[RAG_PIPELINE] No chunks retrieved!")
            return []
        
        print(f"[RAG_PIPELINE] Retrieved {len(candidate_chunks)} candidate chunks")
        
        # --------------------------------
        # Step 3: Normalize and Deduplicate
        # --------------------------------
        print("[RAG_PIPELINE] Step 3: Normalizing and deduplicating...")
        candidate_chunks = normalize_chunks(candidate_chunks)
        candidate_chunks = deduplicate_chunks(candidate_chunks)
        print(f"[RAG_PIPELINE] After dedup: {len(candidate_chunks)} chunks")
        
        # --------------------------------
        # Step 4: Section-based Filtering
        # --------------------------------
        if section_filter is None:
            print("[RAG_PIPELINE] Step 4: Detecting best section...")
            section_filter = find_best_section(candidate_chunks)
            print(f"[RAG_PIPELINE] Detected section: {section_filter}")
        else:
            print(f"[RAG_PIPELINE] Step 4: Using specified section: {section_filter}")
        
        # --------------------------------
        # Step 5: Reranking
        # --------------------------------
        print("[RAG_PIPELINE] Step 5: Reranking with cross-encoder...")
        final_chunks = rerank(query, candidate_chunks, top_k=top_k)
        print(f"[RAG_PIPELINE] Reranked to top {len(final_chunks)} chunks")
        
        # --------------------------------
        # Step 6: Pipeline complete
        # --------------------------------
        print(f"[RAG_PIPELINE] Pipeline complete! Returning {len(final_chunks)} chunks\n")
        
        return final_chunks
    
    def _hybrid_retrieval(
        self,
        queries: List[str],
        ipo_id: str,
        k: int = VECTOR_SEARCH_K
    ) -> List[Dict]:
        """
        Perform hybrid search combining vector and BM25.
        
        Args:
            queries: List of query variations
            ipo_id: Document ID
            k: Number of results per query
        
        Returns:
            Merged and deduplicated results from all queries
        """
        all_chunks = []
        
        # Perform hybrid search for each query variation
        for query in queries:
            try:
                results = hybrid_search(
                    query=query,
                    ipo_id=ipo_id,
                    top_k=k
                )
                all_chunks.extend(results)
                print(f"  Query '{query[:40]}': {len(results)} results")
            except Exception as e:
                print(f"  Error searching with '{query}': {str(e)}")
                continue
        
        # Remove duplicates
        return deduplicate_chunks(normalize_chunks(all_chunks))


# --------------------------------------------------
# Global pipeline instance
# --------------------------------------------------

_pipeline = None

def get_pipeline() -> RAGPipeline:
    """Get or create global pipeline instance."""
    global _pipeline
    if _pipeline is None:
        _pipeline = RAGPipeline()
    return _pipeline

