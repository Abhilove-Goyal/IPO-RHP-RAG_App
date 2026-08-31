from pydantic_settings import BaseSettings 
from pathlib import Path


class Settings(BaseSettings):
    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    llm_model: str = "llama-3.3-70b-versatile"

    jina_api_key: str | None = None
    jina_embedding_model: str = "jina-embeddings-v3"
    jina_reranker_model: str = "jina-reranker-v2-base-multilingual"

    embedding_model: str = "jina-embeddings-v3"
    rerank_model: str = "jina-reranker-v2-base-multilingual"

    collection_name: str = "rag_drhp"
    chunk_size: int = 800
    chunk_overlap: int = 120
    top_k: int = 5
    vector_top_k: int = 20
    bm25_top_k: int = 20
    rerank_top_k: int = 5
    final_top_k: int = 5
    data_path: str = "data"
    log_path: str = "logs/results.json"
    docs_dir: Path = Path("data/docs")

    supabase_url: str | None = None
    supabase_anon_key: str | None = None

    r2_account_id: str | None = None
    r2_bucket_name: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_endpoint_url: str | None = None
    r2_public_url: str | None = None

    class Config:
        env_file = ".env"


settings = Settings()