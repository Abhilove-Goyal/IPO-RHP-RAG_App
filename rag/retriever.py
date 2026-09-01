"""Hybrid retrieval for canonical document chunks.

This module keeps the current Supabase-backed architecture intact while adding:
- BM25 retrieval over document_chunks
- vector retrieval using the canonical match_document_chunks RPC
- candidate fusion using RRF
- metadata-preserving reranking

It intentionally avoids redesigning the database schema and does not add
Chroma or any second vector store.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List

from core.settings import settings
from core.supabase_client import retrieve_document_chunks
from rag.bm25_retriever import BM25Retriever
from rag.jina_embeddings import embed_query
from rag.jina_reranker import rerank_documents

VECTOR_TOP_K = settings.vector_top_k
BM25_TOP_K = settings.bm25_top_k
FUSION_TOP_K = settings.fusion_top_k
RRF_K = settings.rrf_k
RERANK_TOP_K = settings.rerank_top_k


def _candidate_key(candidate: Dict[str, Any]) -> str:
    if not isinstance(candidate, dict):
        return str(candidate)
    key = candidate.get("id") or candidate.get("chunk_id") or candidate.get("source_identifier")
    if key:
        return str(key)
    return str(candidate.get("chunk_text") or "")


def _metadata_for_chunk(chunk: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(chunk, dict):
        return {}
    metadata = dict(chunk.get("metadata") or {})
    metadata.setdefault("document_id", chunk.get("document_id"))
    metadata.setdefault("document_name", chunk.get("document_name"))
    metadata.setdefault("page_number", chunk.get("page_number"))
    metadata.setdefault("section", chunk.get("section"))
    metadata.setdefault("subsection", chunk.get("subsection"))
    metadata.setdefault("chunk_index", chunk.get("chunk_index"))
    metadata.setdefault("source_type", chunk.get("source_type"))
    metadata.setdefault("source_identifier", chunk.get("source_identifier"))
    metadata.setdefault("asset_type", chunk.get("asset_type"))
    metadata.setdefault("caption", chunk.get("caption"))
    return metadata


def _normalize_chunk_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {"chunk_text": str(raw), "metadata": {}}

    metadata = _metadata_for_chunk(raw)
    normalized = {
        "id": raw.get("id"),
        "chunk_id": raw.get("id") or raw.get("chunk_id"),
        "document_id": raw.get("document_id"),
        "chunk_text": raw.get("chunk_text") or "",
        "page_number": raw.get("page_number"),
        "section": raw.get("section"),
        "subsection": raw.get("subsection"),
        "chunk_index": raw.get("chunk_index"),
        "source_type": raw.get("source_type") or metadata.get("source_type"),
        "document_name": raw.get("document_name") or metadata.get("document_name"),
        "metadata": metadata,
    }
    for key in ("source_identifier", "asset_type", "caption"):
        if raw.get(key) is not None:
            normalized[key] = raw.get(key)
        elif key in metadata:
            normalized[key] = metadata.get(key)
    return normalized


def vector_search(query: str, document_id: str, top_k: int = VECTOR_TOP_K) -> List[Dict[str, Any]]:
    if not query or not query.strip():
        return []

    start = time.perf_counter()
    query_embedding = embed_query(query)
    rows = retrieve_document_chunks(query_embedding, document_id, match_count=top_k)
    normalized: List[Dict[str, Any]] = []
    for rank, row in enumerate(rows or [], start=1):
        chunk = _normalize_chunk_row(row)
        chunk["vector_score"] = float(row.get("similarity_score") or row.get("similarity") or row.get("score") or 0.0)
        chunk["vector_rank"] = rank
        chunk["retrieval_source"] = "vector"
        normalized.append(chunk)
    print(f"[RETRIEVER] Vector retrieval: {len(normalized)} candidates in {time.perf_counter() - start:.3f}s")
    return normalized


def bm25_search(query: str, document_id: str, top_k: int = BM25_TOP_K) -> List[Dict[str, Any]]:
    return BM25Retriever().search(document_id=document_id, query=query, top_k=top_k)


def _deduplicate_candidates(candidates: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        key = _candidate_key(candidate)
        if not key:
            continue
        if key not in unique:
            unique[key] = candidate
    return list(unique.values())


def _rrf_fusion(
    vector_results: Iterable[Dict[str, Any]],
    bm25_results: Iterable[Dict[str, Any]],
    *,
    k: int = RRF_K,
    top_k: int | None = None,
) -> List[Dict[str, Any]]:
    combined: Dict[str, Dict[str, Any]] = {}

    for rank, candidate in enumerate(vector_results or [], start=1):
        item = _normalize_chunk_row(candidate)
        key = _candidate_key(item)
        if not key:
            continue
        if key not in combined:
            combined[key] = dict(item)
        combined[key]["vector_score"] = float(candidate.get("vector_score") or candidate.get("score") or item.get("vector_score") or 0.0)
        combined[key]["vector_rank"] = rank
        combined[key]["retrieval_source"] = "vector"

    for rank, candidate in enumerate(bm25_results or [], start=1):
        item = _normalize_chunk_row(candidate)
        key = _candidate_key(item)
        if not key:
            continue
        if key not in combined:
            combined[key] = dict(item)
        combined[key]["bm25_score"] = float(candidate.get("bm25_score") or candidate.get("score") or item.get("bm25_score") or 0.0)
        combined[key]["bm25_rank"] = rank
        combined[key]["retrieval_source"] = "bm25" if "vector_rank" not in combined[key] else "hybrid"

    fused_rows: List[Dict[str, Any]] = []
    for candidate in combined.values():
        vector_rank = candidate.get("vector_rank")
        bm25_rank = candidate.get("bm25_rank")
        score = 0.0
        if vector_rank is not None:
            score += 1.0 / (k + vector_rank)
        if bm25_rank is not None:
            score += 1.0 / (k + bm25_rank)
        candidate["fused_score"] = float(score)
        candidate["vector_score"] = candidate.get("vector_score") or 0.0
        candidate["bm25_score"] = candidate.get("bm25_score") or 0.0
        candidate["metadata"] = _metadata_for_chunk(candidate)
        fused_rows.append(candidate)

    fused_rows.sort(
        key=lambda row: (row.get("fused_score", 0.0), row.get("vector_score", 0.0), row.get("bm25_score", 0.0)),
        reverse=True,
    )
    if top_k is not None:
        fused_rows = fused_rows[:top_k]
    return fused_rows


def hybrid_search(
    query: str,
    document_id: str,
    vector_top_k: int = VECTOR_TOP_K,
    bm25_top_k: int = BM25_TOP_K,
    fusion_top_k: int = FUSION_TOP_K,
    rrf_k: int = RRF_K,
) -> List[Dict[str, Any]]:
    """Merge vector and BM25 candidates using RRF and preserve metadata."""
    if not query or not query.strip():
        return []

    start = time.perf_counter()
    vector_results = vector_search(query, document_id, top_k=vector_top_k)
    bm25_results = bm25_search(query, document_id, top_k=bm25_top_k)
    fused = _rrf_fusion(vector_results, bm25_results, k=rrf_k, top_k=fusion_top_k)
    fused = _deduplicate_candidates(fused)
    print(f"[RETRIEVER] Hybrid fusion: {len(fused)} candidates in {time.perf_counter() - start:.3f}s")
    return fused


def rerank_hybrid_candidates(
    query: str,
    fused_candidates: Iterable[Dict[str, Any]],
    top_k: int = RERANK_TOP_K,
) -> List[Dict[str, Any]]:
    """Rerank only the fused candidate set using the Jina reranker."""
    items = list(fused_candidates or [])
    if not items:
        return []

    rerank_input = []
    for candidate in items:
        text = candidate.get("chunk_text") or ""
        if not text:
            continue
        rerank_input.append({
            "id": candidate.get("id") or candidate.get("chunk_id"),
            "chunk_text": text,
            "page_number": candidate.get("page_number"),
            "section": candidate.get("section"),
            "subsection": candidate.get("subsection"),
            "chunk_index": candidate.get("chunk_index"),
            "source_type": candidate.get("source_type"),
            "document_id": candidate.get("document_id"),
            "metadata": _metadata_for_chunk(candidate),
        })

    if not rerank_input:
        return []

    reranked = rerank_documents(query, rerank_input)
    final: List[Dict[str, Any]] = []
    for rank, item in enumerate(reranked[:top_k], start=1):
        candidate = dict(item.get("candidate") or {})
        merged = {**candidate, **_normalize_chunk_row(candidate)}
        merged["rerank_score"] = float(item.get("score") or 0.0)
        merged["rerank_rank"] = rank
        merged["vector_score"] = merged.get("vector_score") or candidate.get("vector_score") or 0.0
        merged["bm25_score"] = merged.get("bm25_score") or candidate.get("bm25_score") or 0.0
        merged["fused_score"] = merged.get("fused_score") or candidate.get("fused_score") or 0.0
        merged["metadata"] = _metadata_for_chunk(merged)
        final.append(merged)
    return final


def retrieve_multi(
    query: str,
    document_id: str,
    expand_fn=None,
    section_filter=None,
    limit: int | None = None,
):
    """Legacy wrapper retained for old call sites."""
    queries = expand_fn(query) if callable(expand_fn) else [query]
    results: List[Dict[str, Any]] = []
    for item in queries:
        results.extend(hybrid_search(item, document_id, vector_top_k=settings.vector_top_k, bm25_top_k=settings.bm25_top_k, fusion_top_k=settings.fusion_top_k, rrf_k=settings.rrf_k))
    deduped = _deduplicate_candidates(results)
    deduped.sort(key=lambda row: row.get("fused_score", 0.0), reverse=True)
    if limit is not None:
        deduped = deduped[:limit]
    return deduped
