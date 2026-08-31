import json
import urllib.request
from functools import lru_cache
from typing import List

from core.settings import settings

try:
    from sentence_transformers import CrossEncoder
except Exception:  # pragma: no cover
    CrossEncoder = None


@lru_cache(maxsize=1)
def _get_cross_encoder():
    if CrossEncoder is None:
        raise RuntimeError("sentence-transformers is not installed")
    return CrossEncoder("BAAI/bge-reranker-large")


def _jina_rerank(query: str, chunks: List[dict], top_k: int = 5) -> List[dict]:
    if not chunks:
        return []

    if not settings.jina_api_key:
        raise RuntimeError("Jina API key is not configured")

    docs = [chunk.get("chunk_text", "") for chunk in chunks]
    payload = json.dumps({
        "model": settings.jina_reranker_model,
        "query": query,
        "docs": docs,
        "top_n": min(top_k, len(docs))
    }).encode("utf-8")

    request = urllib.request.Request(
        "https://api.jina.ai/v1/rerank",
        data=payload,
        headers={
            "Authorization": f"Bearer {settings.jina_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.loads(response.read().decode("utf-8"))

    ranked_indexes = {}
    for item in result.get("results", []):
        ranked_indexes[int(item["index"])] = float(item.get("relevance_score", 0.0))

    ordered = sorted(range(len(chunks)), key=lambda idx: ranked_indexes.get(idx, 0.0), reverse=True)
    return [chunks[idx] for idx in ordered[:top_k]]


def rerank(query: str, chunks: List[dict], top_k: int = 5) -> List[dict]:
    """
    Rerank chunks using Jina when configured; otherwise fall back to the local BGE cross-encoder.
    """
    if not chunks:
        return []

    if settings.jina_api_key and settings.jina_reranker_model:
        try:
            return _jina_rerank(query, chunks, top_k=top_k)
        except Exception as exc:
            print(f"[RERANKER] Jina rerank failed, falling back to local model: {exc}")

    encoder = _get_cross_encoder()
    pairs = [(query, chunk.get("chunk_text", "")) for chunk in chunks]
    scores = encoder.predict(pairs)
    scored_chunks = list(zip(chunks, scores))
    scored_chunks.sort(key=lambda x: x[1], reverse=True)
    return [chunk for chunk, _ in scored_chunks[:top_k]]
