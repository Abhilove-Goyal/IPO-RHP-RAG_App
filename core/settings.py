from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    llm_model: str = "llama-3.3-70b-versatile"

    jina_api_key: str | None = None
    jina_embedding_model: str = "jina-embeddings-v3"
    jina_reranker_model: str = "jina-reranker-v2-base-multilingual"
    embedding_dimension: int = 1024

    chunk_size: int = 800
    chunk_overlap: int = 120
    top_k: int = 5
    vector_top_k: int = 40  # increased from 20 to broaden vector candidates
    bm25_top_k: int = 40   # increased from 20 to broaden BM25 candidates
    fusion_top_k: int = 50 # increased from 30 to retain more fused results
    rrf_k: int = 60
    rerank_top_k: int = 5
    # New retry configuration for LLM calls
    llm_max_retries: int = 3  # number of attempts per model
    llm_initial_backoff: int = 2  # seconds, exponential backoff base
    # Optional fallback model (different from primary Groq model)
    rag_fallback_model: str = "gpt-3.5-turbo"
    final_top_k: int = 5
    prompt_evidence_token_budget: int = 4000
    data_path: str = "data"
    log_path: str = "logs/results.json"
    docs_dir: Path = Path("data/docs")

    supabase_url: str | None = None
    supabase_service_role_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SUPABASE_SERVICE_ROLE_KEY",
            "supabase_service_role_key",
        ),
    )
    supabase_publishable_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SUPABASE_PUBLISHABLE_KEY",
            "supabase_publishable_key",
            "SUPABASE_ANON_KEY",
            "supabase_anon_key",
        ),
    )
    supabase_anon_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SUPABASE_ANON_KEY",
            "supabase_anon_key",
            "SUPABASE_PUBLISHABLE_KEY",
            "supabase_publishable_key",
        ),
    )

    @property
    def supabase_key(self) -> str | None:
        return self.supabase_publishable_key or self.supabase_anon_key

    r2_account_id: str | None = None
    r2_bucket_name: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_endpoint_url: str | None = None
    r2_public_url: str | None = None


settings = Settings()