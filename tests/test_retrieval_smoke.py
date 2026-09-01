import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.supabase_client import execute_supabase, retrieve_document_chunks, supabase
from rag.bm25_retriever import BM25Retriever
from rag.jina_embeddings import embed_query
from rag.jina_reranker import rerank_documents
from rag.retriever import _deduplicate_candidates, _normalize_chunk_row, _rrf_fusion

DOCUMENT_ID = "cc59e2bb-a891-5162-b64a-0fa7ebb30362"
QUERIES = [
    "How dependent is the company on its major customers?",
    "What was the company's FY2025 EBITDA margin?",
    "What was the company's revenue in FY2025 according to the financial table?",
]


def preview(text: str) -> str:
    return " ".join((text or "").split())[:140]


def compact(row: dict, score_key: str, rank_key: str) -> dict:
    return {
        "chunk_id": row.get("chunk_id") or row.get("id"),
        "page_number": row.get("page_number"),
        "section": row.get("section"),
        "subsection": row.get("subsection"),
        "source_type": row.get("source_type"),
        "score": row.get(score_key),
        "rank": row.get(rank_key),
        "preview": preview(row.get("chunk_text")),
    }


def main() -> None:
    inventory = execute_supabase(
        "count live smoke-test chunks",
        supabase.table("document_chunks")
        .select("id", count="exact")
        .eq("document_id", DOCUMENT_ID)
        .limit(1),
    )
    print("LIVE_CHUNK_COUNT", inventory.count)

    for query in QUERIES:
        total_start = time.perf_counter()
        query_start = time.perf_counter()
        normalized_query = query.strip()
        query_processing_ms = (time.perf_counter() - query_start) * 1000

        stage_start = time.perf_counter()
        embedding = embed_query(normalized_query)
        embedding_ms = (time.perf_counter() - stage_start) * 1000

        stage_start = time.perf_counter()
        vector_raw = retrieve_document_chunks(embedding, DOCUMENT_ID, match_count=20)
        vector_ms = (time.perf_counter() - stage_start) * 1000
        vector = []
        for rank, raw in enumerate(vector_raw or [], start=1):
            row = _normalize_chunk_row(raw)
            row.update(
                vector_score=float(raw.get("similarity_score") or raw.get("similarity") or raw.get("score") or 0.0),
                vector_rank=rank,
            )
            vector.append(row)

        stage_start = time.perf_counter()
        bm25_raw = BM25Retriever().search(document_id=DOCUMENT_ID, query=normalized_query, top_k=20)
        bm25_ms = (time.perf_counter() - stage_start) * 1000
        bm25 = []
        for rank, raw in enumerate(bm25_raw or [], start=1):
            row = _normalize_chunk_row(raw)
            row.update(bm25_score=float(raw.get("bm25_score") or 0.0), bm25_rank=rank)
            bm25.append(row)

        stage_start = time.perf_counter()
        fused = _deduplicate_candidates(_rrf_fusion(vector, bm25, k=60, top_k=30))
        rrf_ms = (time.perf_counter() - stage_start) * 1000

        rerank_input = [
            {
                "id": row.get("chunk_id"),
                "chunk_text": row.get("chunk_text"),
                "document_id": row.get("document_id"),
                "page_number": row.get("page_number"),
                "section": row.get("section"),
                "subsection": row.get("subsection"),
                "source_type": row.get("source_type"),
                "metadata": row.get("metadata"),
                "vector_score": row.get("vector_score"),
                "bm25_score": row.get("bm25_score"),
                "fused_score": row.get("fused_score"),
            }
            for row in fused
        ]
        stage_start = time.perf_counter()
        reranked = rerank_documents(normalized_query, rerank_input)
        rerank_ms = (time.perf_counter() - stage_start) * 1000

        stage_start = time.perf_counter()
        final = [item["candidate"] for item in reranked[:5]]
        context = "\n\n".join(
            f"[{row.get('section')} | page {row.get('page_number')} | {row.get('source_type')}] {row.get('chunk_text', '')}"
            for row in final
        )
        context_ms = (time.perf_counter() - stage_start) * 1000
        total_ms = (time.perf_counter() - total_start) * 1000

        print("\nQUERY", query)
        print(
            "TIMINGS_MS",
            {
                "query_processing_ms": round(query_processing_ms, 3),
                "embedding_ms": round(embedding_ms, 2),
                "vector_search_ms": round(vector_ms, 2),
                "bm25_ms": round(bm25_ms, 2),
                "rrf_ms": round(rrf_ms, 3),
                "rerank_ms": round(rerank_ms, 2),
                "context_construction_ms": round(context_ms, 3),
                "llm_generation_ms": None,
                "total_retrieval_ms": round(total_ms, 2),
            },
        )
        print(
            "COUNTS",
            {
                "vector_candidates": len(vector),
                "bm25_candidates": len(bm25),
                "fused_candidates": len(fused),
                "reranked_candidates": len(reranked),
                "rerank_sent": len(rerank_input),
            },
        )
        print("VECTOR_TOP", compact(vector[0], "vector_score", "vector_rank") if vector else None)
        print("BM25_TOP", compact(bm25[0], "bm25_score", "bm25_rank") if bm25 else None)
        print(
            "RRF_TOP",
            [
                {
                    "chunk_id": row.get("chunk_id"),
                    "vector_score": row.get("vector_score"),
                    "bm25_score": row.get("bm25_score"),
                    "vector_rank": row.get("vector_rank"),
                    "bm25_rank": row.get("bm25_rank"),
                    "rrf_score": row.get("fused_score"),
                }
                for row in fused[:5]
            ],
        )
        print(
            "RERANK_TOP",
            [
                {
                    "rank": rank,
                    "chunk_id": item["candidate"].get("id"),
                    "page_number": item["candidate"].get("page_number"),
                    "section": item["candidate"].get("section"),
                    "subsection": item["candidate"].get("subsection"),
                    "source_type": item["candidate"].get("source_type"),
                    "jina_score": item.get("score"),
                    "preview": preview(item["candidate"].get("chunk_text")),
                }
                for rank, item in enumerate(reranked[:5], start=1)
            ],
        )
        print("FINAL_CONTEXT_PREVIEW", preview(context))


if __name__ == "__main__":
    main()
