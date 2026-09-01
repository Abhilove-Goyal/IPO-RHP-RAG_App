from supabase import create_client
from core.settings import settings


class DatabaseConnectionError(RuntimeError):
    """Raised when the configured Supabase endpoint cannot be reached."""


class DatabaseOperationError(RuntimeError):
    """Raised when a Supabase operation fails."""


def execute_supabase(operation: str, request):
    try:
        return request.execute()
    except Exception as e:
        message = str(e)
        if "getaddrinfo failed" in message or "Name or service not known" in message:
            raise DatabaseConnectionError(
                f"Could not resolve Supabase host while trying to {operation}. "
                "Check SUPABASE_URL in .env and your network/DNS connection."
            ) from e
        raise DatabaseOperationError(f"Supabase failed while trying to {operation}: {message}") from e


# --------------------------------------------------
# Supabase Client
# --------------------------------------------------

supabase_public_key = settings.supabase_key or settings.supabase_anon_key or settings.supabase_publishable_key
supabase_key = settings.supabase_service_role_key or supabase_public_key

if not settings.supabase_url or not supabase_key:
    raise RuntimeError("Supabase credentials missing")

supabase = create_client(
    settings.supabase_url,
    supabase_key
)

supabase_public = (
    create_client(settings.supabase_url, supabase_public_key)
    if settings.supabase_url and supabase_public_key
    else None
)


def supabase_configuration_status() -> dict[str, bool]:
    """Return safe credential presence flags without exposing secret values."""
    return {
        "supabase_url_configured": bool(settings.supabase_url),
        "public_key_configured": bool(supabase_public_key),
        "service_role_key_configured": bool(settings.supabase_service_role_key),
        "backend_uses_service_role": bool(settings.supabase_service_role_key),
    }


# --------------------------------------------------
# Document metadata helpers
# --------------------------------------------------

def create_document(
    *,
    document_id: str,
    user_id: str | None = None,
    company_name: str | None = None,
    document_name: str | None = None,
    document_hash: str | None = None,
    storage_key: str | None = None,
    file_size: int | None = None,
    page_count: int | None = None,
    processing_status: str = "uploaded",
    processing_error: str | None = None,
):
    payload = {
        "id": document_id,
        "user_id": user_id,
        "company_name": company_name,
        "document_name": document_name,
        "document_hash": document_hash,
        "storage_key": storage_key,
        "file_size": file_size,
        "page_count": page_count,
        "processing_status": processing_status,
        "processing_error": processing_error,
    }
    return execute_supabase("create or upsert document metadata", supabase.table("documents").upsert(payload))


def get_document_by_hash(document_hash: str):
    return execute_supabase(
        "fetch document by hash",
        supabase.table("documents").select("*").eq("document_hash", document_hash).limit(1)
    )


def get_document_by_id(document_id: str):
    return execute_supabase(
        "fetch document by id",
        supabase.table("documents").select("*").eq("id", document_id).limit(1)
    )


# --------------------------------------------------
# Document asset helpers
# --------------------------------------------------

def create_document_asset(
    *,
    document_id: str,
    asset_type: str,
    page_number: int | None = None,
    storage_key: str | None = None,
    content_type: str | None = None,
    metadata: dict | None = None,
):
    payload = {
        "document_id": document_id,
        "asset_type": asset_type,
        "page_number": page_number,
        "storage_key": storage_key,
        "metadata": {**(metadata or {}), "content_type": content_type},
    }
    return execute_supabase("insert document asset", supabase.table("document_assets").insert(payload))


# --------------------------------------------------
# Document chunk helpers
# --------------------------------------------------

def insert_document_chunk(
    *,
    document_id: str,
    chunk_index: int,
    chunk_text: str,
    page_number: int | None = None,
    section: str | None = None,
    subsection: str | None = None,
    chunk_tokens: int | None = None,
    embedding: list | None = None,
    embedding_model: str | None = None,
    metadata: dict | None = None,
):
    payload = {
        "document_id": document_id,
        "chunk_index": chunk_index,
        "chunk_text": chunk_text,
        "page_number": page_number,
        "section": section,
        "subsection": subsection,
        "chunk_tokens": chunk_tokens,
        "embedding": embedding,
        "embedding_model": embedding_model or getattr(settings, "jina_embedding_model", "jina-embeddings-v3"),
        "metadata": metadata or {},
    }
    return execute_supabase("insert document chunk", supabase.table("document_chunks").insert(payload))


# Compatibility alias for older callers that pass chunk_number instead of chunk_index

def insert_chunk(
    document_id: str,
    chunk_text: str,
    chunk_number: int,
    page_number: int,
    section: str,
    embedding: list,
    document_name: str = "unknown",
    chunk_tokens: int = 0,
    embedding_model: str | None = None,
    subsection: str | None = None,
    metadata: dict | None = None,
):
    return insert_document_chunk(
        document_id=document_id,
        chunk_index=chunk_number,
        chunk_text=chunk_text,
        page_number=page_number,
        section=section,
        subsection=subsection,
        chunk_tokens=chunk_tokens,
        embedding=embedding,
        embedding_model=embedding_model or getattr(settings, "jina_embedding_model", "jina-embeddings-v3"),
        metadata={**(metadata or {}), "document_name": document_name},
    )


# --------------------------------------------------
# Vector retrieval using the new Supabase RPC
# --------------------------------------------------

def retrieve_document_chunks(query_embedding, document_id: str, match_count: int = 20):
    result = execute_supabase(
        "retrieve matching document chunks",
        supabase.rpc(
            "match_document_chunks",
            {
                "query_embedding": query_embedding,
                "p_document_id": document_id,
                "match_count": match_count,
            },
        ),
    )
    return result.data if getattr(result, "data", None) is not None else []


# Compatibility alias for older callers that still treat this as a generic chunk retrieval function.

def retrieve_chunks(query_embedding, document_id: str, limit: int = 20):
    return retrieve_document_chunks(query_embedding, document_id, match_count=limit)


# --------------------------------------------------
# Query logging
# --------------------------------------------------

def log_rag_query(user_id, document_id, question, answer):
    payload = {
        "user_id": user_id,
        "document_id": document_id,
        "question": question,
        "answer": answer,
    }
    return execute_supabase("log rag query", supabase.table("rag_queries").insert(payload))


# Backward-compatible name for older call sites

def log_query(user_id, document_id, question, answer):
    return log_rag_query(user_id, document_id, question, answer)


# --------------------------------------------------
# Trace logging
# --------------------------------------------------

def create_or_update_rag_trace(*, trace_id=None, document_id=None, model=None, retrieved_chunks=None, reranked_chunks=None, chunks_used=None, faithfulness=None, latency_ms=None):
    payload = {
        "trace_id": trace_id,
        "document_id": document_id,
        "model": model,
        "retrieved_chunks": retrieved_chunks,
        "reranked_chunks": reranked_chunks,
        "chunks_used": chunks_used,
        "faithfulness": faithfulness,
        "latency_ms": latency_ms,
    }
    return execute_supabase("create or update rag trace", supabase.table("rag_traces").upsert(payload))


def log_result(data: dict):
    payload = {
        "trace_id": data.get("trace_id"),
        "document_id": data.get("document_id") or data.get("document_id") or data.get("ipo_id"),
        "model": data.get("model"),
        "retrieved_chunks": data.get("retrieved_chunks"),
        "reranked_chunks": data.get("reranked_chunks"),
        "chunks_used": data.get("chunks_used"),
        "faithfulness": data.get("faithfulness"),
        "latency_ms": data.get("latency_ms"),
    }
    return create_or_update_rag_trace(**payload)


# --------------------------------------------------
# Document operations
# --------------------------------------------------

def list_documents():
    return execute_supabase("list documents", supabase.table("documents").select("*"))


def get_document_stats(document_id: str):
    return execute_supabase(
        "read document stats",
        supabase.table("document_chunks")
        .select("id", count="exact")
        .eq("document_id", document_id)
    )


def list_document_chunks(document_id: str, *, limit: int = 500, start_page: int | None = None, end_page: int | None = None):
    query = supabase.table("document_chunks").select("*").eq("document_id", document_id)
    if start_page is not None:
        query = query.gte("page_number", start_page)
    if end_page is not None:
        query = query.lte("page_number", end_page)
    return execute_supabase(
        "fetch document chunks for document",
        query.limit(limit)
    )


def delete_document(document_id: str):
    execute_supabase(
        "delete document chunks",
        supabase.table("document_chunks").delete().eq("document_id", document_id)
    )
    return execute_supabase(
        "delete document metadata",
        supabase.table("documents").delete().eq("id", document_id)
    )


# Backward-compatible aliases for older code paths

def insert_ipo(ipo_id: str, document_path: str, user_id: str | None = None):
    return create_document(
        document_id=ipo_id,
        user_id=user_id,
        document_name=document_path.split("/")[-1] if document_path else ipo_id,
        storage_key=document_path,
        processing_status="uploaded",
    )


def insert_ipo_chunk(
    ipo_id: str,
    chunk_text: str,
    chunk_number: int,
    page_number: int,
    section: str,
    embedding: list,
    document_name: str = "unknown",
    chunk_tokens: int = 0,
):
    return insert_document_chunk(
        document_id=ipo_id,
        chunk_index=chunk_number,
        chunk_text=chunk_text,
        page_number=page_number,
        section=section,
        chunk_tokens=chunk_tokens,
        embedding=embedding,
        metadata={"document_name": document_name},
    )


def list_ipos():
    return list_documents()


def ipo_stats(ipo_id: str):
    return get_document_stats(ipo_id)


def delete_ipo(ipo_id: str):
    return delete_document(ipo_id)


# --------------------------------------------------
# This module intentionally avoids legacy table and RPC names.
# The old ipos/ipo_chunks/queries and hybrid_match_* names are deprecated.
