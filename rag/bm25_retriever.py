import re
from typing import Any, Dict, List

from rank_bm25 import BM25Okapi

from core.supabase_client import execute_supabase, supabase


class BM25Retriever:
    """BM25 keyword retrieval for canonical document_chunks rows."""

    def search(self, *, document_id: str, query: str, top_k: int = 20) -> List[Dict[str, Any]]:
        if not document_id or not query or not query.strip():
            return []

        query_tokens = re.findall(r"\w+", query.lower())
        if not query_tokens:
            return []

        result = execute_supabase(
            "load chunks for BM25 search",
            supabase.table("document_chunks").select("*").eq("document_id", document_id),
        )
        rows = result.data if getattr(result, "data", None) is not None else []
        if not rows:
            return []

        tokenized_docs = []
        valid_rows = []
        for row in rows:
            text = row.get("chunk_text") or ""
            if not text or not text.strip():
                continue
            tokens = re.findall(r"\w+", text.lower())
            if not tokens:
                continue
            valid_rows.append(row)
            tokenized_docs.append(tokens)

        if not valid_rows:
            return []

        bm25 = BM25Okapi(tokenized_docs)
        scores = bm25.get_scores(query_tokens)
        ranked = [(row, float(score)) for row, score in zip(valid_rows, scores)]
        ranked.sort(key=lambda item: item[1], reverse=True)

        final: List[Dict[str, Any]] = []
        for index, (row, score) in enumerate(ranked[:top_k], start=1):
            payload = dict(row)
            payload["bm25_score"] = score
            payload["bm25_rank"] = index
            final.append(payload)
        return final


def bm25_search(query: str, document_id: str, top_k: int = 20) -> List[Dict[str, Any]]:
    return BM25Retriever().search(document_id=document_id, query=query, top_k=top_k)
