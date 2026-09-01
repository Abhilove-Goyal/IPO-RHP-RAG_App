import json
import urllib.error
import urllib.request
from typing import Iterable, List

from core.settings import settings

DEFAULT_MODEL = "jina-embeddings-v3"


def _normalize_texts(texts: Iterable[str]) -> List[str]:
    if isinstance(texts, str):
        items = [texts]
    else:
        items = list(texts)

    if not items:
        raise ValueError("No input text provided for Jina embedding request.")

    normalized = []
    for text in items:
        if not isinstance(text, str):
            raise TypeError("Each embedding input must be a string.")
        normalized.append(text)
    return normalized


def _validate_embedding_dimension(vector: List[float], expected_dimension: int | None = None) -> List[float]:
    target = expected_dimension if expected_dimension is not None else settings.embedding_dimension
    if not isinstance(vector, list):
        raise ValueError(f"Embedding response is not a list: {type(vector).__name__}")
    if len(vector) != target:
        raise ValueError(f"Embedding dimension mismatch: expected {target}, got {len(vector)}.")
    if not all(isinstance(value, (int, float)) for value in vector):
        raise ValueError("Embedding vector contains non-numeric values.")
    return vector


def _call_jina_embeddings(texts: Iterable[str]) -> List[List[float]]:
    api_key = settings.jina_api_key
    if not api_key:
        raise RuntimeError("Jina API key is not configured in settings.")

    model_name = settings.jina_embedding_model or DEFAULT_MODEL
    payload = {
        "model": model_name,
        "input": _normalize_texts(texts),
    }

    request = urllib.request.Request(
        "https://api.jina.ai/v1/embeddings",
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
        raise RuntimeError(f"Jina embedding request failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Jina embedding request failed: {exc.reason}") from exc

    items = response_data.get("data", [])
    if len(items) != len(payload["input"]):
        raise ValueError(
            "Jina embedding response did not return the expected number of vectors."
        )

    embeddings: List[List[float]] = []
    for item in items:
        vector = item.get("embedding")
        if vector is None:
            raise ValueError("Jina embedding response missing the embedding vector.")
        embeddings.append(_validate_embedding_dimension(vector, settings.embedding_dimension))
    return embeddings


def embed_texts(texts: Iterable[str]) -> List[List[float]]:
    """Embed one or more texts using the official Jina embeddings API."""
    return _call_jina_embeddings(texts)


def embed_query(text: str) -> List[float]:
    """Embed a single query string for similarity search."""
    vectors = embed_texts([text])
    return vectors[0]


def test_jina_embedding_request() -> tuple[bool, int]:
    sample_text = "The company reported strong revenue growth during FY2025."
    try:
        vector = embed_query(sample_text)
        return True, len(vector)
    except Exception:
        return False, 0


if __name__ == "__main__":
    succeeded, dimension = test_jina_embedding_request()
    print(f"Request succeeded: {succeeded}")
    print(f"Vector dimension: {dimension}")
