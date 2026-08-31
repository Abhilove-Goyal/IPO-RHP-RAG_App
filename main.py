"""
Main RAG orchestration with hierarchical retrieval pipeline.

Pipeline:
1. Section retrieval (keyword-based)
2. Chunk retrieval (vector search within sections)
3. Reranking (cross-encoder)
4. Answer generation (LLM with metadata)
"""

from rag.ingestion import load_chunk_documents
from rag.retriever import retrieve_chunks_in_sections
from rag.multi_query import generate_queries
from rag.generator import generate_answer
from rag.logger import log_result
from rag.section_retriever import retrieve_top_sections
from rag.toc_parser import extract_toc
from rag.reranker import rerank
from core.supabase_client import execute_supabase, supabase
from core.document_structure import get_toc
import core.runtime_state as runtime
import pdfplumber
from pathlib import Path
from core.settings import settings

_chunks = None


def get_retriever_context():
    return generate_queries, runtime.get_current_ipo()


def reset_chunks():
    global _chunks
    _chunks = None
    print("[MAIN] Chunk cache reset")


def ensure_embeddings_exist(ipo_id: str):
    """Check if chunks are indexed, if not run ingestion."""
    existing = execute_supabase(
        "check indexed chunks",
        supabase.table("document_chunks")
        .select("id", count="exact")
        .eq("document_id", ipo_id)
    )

    if existing.count == 0:
        print(f"[MAIN] No embeddings found for {ipo_id}. Running ingestion.")
        load_chunk_documents()


def get_sections_for_ipo(ipo_id: str):
    """Get hierarchical section structure from document TOC."""
    try:
        # Try to get from runtime cache first
        toc = get_toc(ipo_id)
        if toc:
            print(f"[MAIN] Using cached TOC for {ipo_id}")
            return toc
        
        # Otherwise extract from PDF
        docs_path = settings.docs_dir
        pdf_files = [f for f in docs_path.iterdir() if f.suffix.lower() == '.pdf']
        
        if not pdf_files:
            print(f"[MAIN] No PDF files found in {docs_path}")
            return []
        
        pdf_path = pdf_files[0]  # Use first PDF
        print(f"[MAIN] Extracting TOC from {pdf_path.name}")
        
        with pdfplumber.open(pdf_path) as pdf:
            sections = extract_toc(pdf)
            return sections if sections else []
            
    except Exception as e:
        print(f"[MAIN] Error getting sections: {e}")
        return []


def run(query: str):
    """
    Complete RAG pipeline with hierarchical retrieval.
    
    Returns:
        Tuple of (answer_text, faithfulness_score)
    """
    print(f"\n[MAIN] ========================================")
    print(f"[MAIN] Starting RAG pipeline for query")
    print(f"[MAIN] Query: {query[:80]}")
    print(f"[MAIN] ========================================\n")

    # Get IPO from runtime state
    ipo_id = runtime.get_current_ipo()

    # If server restarted and runtime lost IPO, recover from DB
    if ipo_id is None:
        result = execute_supabase(
            "recover uploaded document",
            supabase.table("documents")
            .select("id")
            .limit(1)
        )

        if not result.data:
            raise ValueError("No document uploaded yet. Please upload a DRHP PDF first.")

        ipo_id = result.data[0]["id"]
        runtime.set_current_ipo(ipo_id)
        print(f"[MAIN] Using document from DB: {ipo_id}")

    # Ensure embeddings exist
    ensure_embeddings_exist(ipo_id)

    # ====== STAGE 1: Section Retrieval ======
    print(f"\n[MAIN] STAGE 1: Section-level retrieval")
    sections = get_sections_for_ipo(ipo_id)
    
    if not sections:
        print("[MAIN] No sections found, using default retrieval")
        top_sections = []
    else:
        top_sections = retrieve_top_sections(query, ipo_id, sections, top_k=3)

    # ====== STAGE 2: Chunk Retrieval (within sections) ======
    print(f"\n[MAIN] STAGE 2: Chunk-level retrieval")
    
    # Generate query variations for better coverage
    query_variations = generate_queries(query)
    print(f"[MAIN] Query variations: {len(query_variations)}")
    
    # Retrieve chunks (within selected sections if available)
    retrieved_chunks = retrieve_chunks_in_sections(
        query=query,
        query_variations=query_variations,
        ipo_id=ipo_id,
        top_sections=top_sections,
        limit=20
    )
    
    print(f"[MAIN] Retrieved {len(retrieved_chunks)} chunks")
    
    if not retrieved_chunks:
        print("[MAIN] No chunks retrieved!")
        return "Unable to find relevant context in the document.", 0.0

    # ====== STAGE 3: Reranking ======
    print(f"\n[MAIN] STAGE 3: Cross-encoder reranking")
    
    final_chunks = rerank(query, retrieved_chunks, top_k=5)
    
    print(f"[MAIN] Reranked to top {len(final_chunks)} chunks")
    for i, chunk in enumerate(final_chunks, 1):
        section = chunk.get("section", "unknown")
        page = chunk.get("page_number", "?")
        print(f"  [{i}] Section: {section}, Page: {page}")

    # ====== STAGE 4: Answer Generation ======
    print(f"\n[MAIN] STAGE 4: Answer generation with LLM")
    
    answer, faithfulness_score = generate_answer(query, final_chunks)

    # Log query
    try:
        log_result({
            "ipo_id": ipo_id,
            "query": query,
            "answer": answer[:200],
            "chunks_used": len(final_chunks),
            "faithfulness": faithfulness_score
        })
    except Exception as e:
        print(f"[MAIN] Logging error: {e}")

    print(f"\n[MAIN] ========================================")
    print(f"[MAIN] Pipeline complete")
    print(f"[MAIN] Answer length: {len(answer)} chars")
    print(f"[MAIN] Faithfulness: {faithfulness_score:.2f}")
    print(f"[MAIN] ========================================\n")

    return answer, faithfulness_score
