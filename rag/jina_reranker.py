import json
import urllib.error
import urllib.request
from typing import Any, Iterable, List

from core.settings import settings

DEFAULT_MODEL = "jina-reranker-v2-base-multilingual"


def _candidate_text(candidate: Any) -> str:
    if isinstance(candidate, str):
        return candidate
    if isinstance(candidate, dict):
        for key in ("text", "content", "chunk_text", "document", "body", "passage"):
            value = candidate.get(key)
            if value is not None:
                return str(value)
        return str(candidate)
    return str(candidate)


def _candidate_title(candidate: Any, index: int) -> str:
    if isinstance(candidate, dict):
        for key in ("title", "name", "heading", "id"):
            value = candidate.get(key)
            if value is not None:
                return str(value)
    return f"candidate_{index}"


def rerank_documents(query: str, candidates: Iterable[Any]) -> List[dict]:
    """Return ranked candidates with their original data and relevance scores."""
    items = list(candidates)
    if not query:
        raise ValueError("Query must not be empty.")
    if not items:
        return []

    api_key = settings.jina_api_key
    if not api_key:
        raise RuntimeError("Jina API key is not configured in settings.")

    model_name = settings.jina_reranker_model or DEFAULT_MODEL
    documents = [_candidate_text(item) for item in items]
    payload = {
        "model": model_name,
        "query": query,
        "documents": documents,
        "top_n": len(documents),
    }

    request = urllib.request.Request(
        "https://api.jina.ai/v1/rerank",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Jina rerank request failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Jina rerank request failed: {exc.reason}") from exc

    results = response_data.get("results", [])
    if not results:
        raise ValueError("Jina reranker response did not include any ranked results.")

    ranked: List[dict] = []
    for item in results:
        index = int(item["index"])
        candidate = items[index]
        ranked.append(
            {
                "index": index,
                "title": _candidate_title(candidate, index),
                "score": float(item.get("relevance_score", 0.0)),
                "candidate": candidate,
            }
        )

    ranked.sort(key=lambda entry: entry["score"], reverse=True)
    return ranked


def test_jina_reranker_request() -> tuple[bool, int, List[dict]]:
    query = "What are the company's major financial risks?"
    candidates = [
        {
            "title": "Revenue concentration",
            "text": "The company depends heavily on a single customer segment, raising revenue concentration risk during economic downturns.",
        },
        {
            "title": "Product launch",
            "text": "The company launched a new product line and expects improved customer engagement in the next quarter.",
        },
        {
            "title": "Currency exposure",
            "text": "Foreign exchange volatility creates significant downside risk for the firm's earnings and cash flows.",
        },
        {
            "title": "Office amenities",
            "text": "The office renovation includes improved break rooms, better seating, and a new coffee bar for employees.",
        },
        {
            "title": "Leverage burden",
            "text": "High debt obligations and interest-rate sensitivity are major financial risks for the company.",
        },
    ]

    try:
        results = rerank_documents(query, candidates)
        return True, len(candidates), results
    except Exception:
        return False, len(candidates), []


if __name__ == "__main__":
    success, count, results = test_jina_reranker_request()
    print(f"Request succeeded: {success}")
    print(f"Number of candidates: {count}")
    for idx, item in enumerate(results[:3]):
        print(f"Ranked result {idx}: title={item['title']} score={item['score']:.6f}")
