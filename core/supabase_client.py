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

if not settings.supabase_url or not settings.supabase_anon_key:
    raise RuntimeError("Supabase credentials missing")

supabase = create_client(
    settings.supabase_url,
    settings.supabase_anon_key
)


# --------------------------------------------------
# New schema helpers
# --------------------------------------------------

def insert_document(
    document_id: str,
    document_name: str,
    user_id: str | None = None,
    company_name: str | None = None,
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
    return execute_supabase("save document metadata", supabase.table("documents").upsert(payload))


# --------------------------------------------------
# Legacy compatibility wrapper for older calls
# --------------------------------------------------

def insert_ipo(ipo_id: str, document_path: str, user_id: str | None = None):
    """Backward-compatible wrapper that stores the same document under the new schema."""
    return insert_document(
        document_id=ipo_id,
        document_name=document_path.split("/")[-1] if document_path else ipo_id,
        user_id=user_id,
        storage_key=document_path,
        processing_status="uploaded",
    )


# --------------------------------------------------
# Insert chunk embeddings into new document_chunks table
# --------------------------------------------------

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
    data = {
        "document_id": document_id,
        "chunk_index": chunk_number,
        "chunk_text": chunk_text,
        "page_number": page_number,
        "section": section,
        "subsection": subsection,
        "chunk_tokens": chunk_tokens,
        "embedding": embedding,
        "embedding_model": embedding_model or getattr(settings, "embedding_model", "unknown"),
        "metadata": metadata or {"document_name": document_name},
    }
    return execute_supabase("save document chunk", supabase.table("document_chunks").insert(data))


# --------------------------------------------------
# Backward compatibility for older callers
# --------------------------------------------------

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
    return insert_chunk(
        document_id=ipo_id,
        chunk_text=chunk_text,
        chunk_number=chunk_number,
        page_number=page_number,
        section=section,
        embedding=embedding,
        document_name=document_name,
        chunk_tokens=chunk_tokens,
    )


# --------------------------------------------------
# Vector retrieval using pgvector RPC
# --------------------------------------------------

def retrieve_chunks(query_embedding, document_id, limit, query_text: str = ""):
    """Retrieve chunks for a document using the current Supabase vector function."""
    rpc_args = {
        "query_embedding": query_embedding,
        "query_text": query_text,
        "document_id": document_id,
        "match_count": limit,
    }

    for rpc_name in ("hybrid_match_document_chunks", "hybrid_match_chunks"):
        try:
            result = execute_supabase(
                f"retrieve matching chunks via {rpc_name}",
                supabase.rpc(rpc_name, rpc_args)
            )
            if result and getattr(result, "data", None):
                return result.data
        except Exception:
            continue

    return []


# --------------------------------------------------
# Query logging
# --------------------------------------------------

def log_query(user_id, document_id, question, answer):
    return execute_supabase(
        "log question",
        supabase.table("rag_queries").insert({
            "user_id": user_id,
            "document_id": document_id,
            "question": question,
            "answer": answer,
        })
    )


# --------------------------------------------------
# Trace logging
# --------------------------------------------------

def log_result(data: dict):
    payload = {
        "trace_id": data.get("trace_id"),
        "document_id": data.get("ipo_id") or data.get("document_id"),
        "model": data.get("model"),
        "retrieved_chunks": data.get("retrieved_chunks"),
        "reranked_chunks": data.get("reranked_chunks"),
        "chunks_used": data.get("chunks_used"),
        "faithfulness": data.get("faithfulness"),
        "latency_ms": data.get("latency_ms"),
    }
    return execute_supabase("log rag trace", supabase.table("rag_traces").insert(payload))


# --------------------------------------------------
# Admin utilities
# --------------------------------------------------

def list_documents():
    return execute_supabase("list documents", supabase.table("documents").select("*"))


def list_ipos():
    return list_documents()


def delete_document(document_id: str):
    execute_supabase(
        "delete document chunks",
        supabase.table("document_chunks").delete().eq("document_id", document_id)
    )
    return execute_supabase(
        "delete document metadata",
        supabase.table("documents").delete().eq("id", document_id)
    )


def delete_ipo(ipo_id: str):
    return delete_document(ipo_id)


def document_stats(document_id: str):
    return execute_supabase(
        "read document stats",
        supabase.table("document_chunks")
        .select("id", count="exact")
        .eq("document_id", document_id)
    )


def ipo_stats(ipo_id: str):
    return document_stats(ipo_id)


# --------------------------------------------------
# Backward-compatible aliases for older code paths
# --------------------------------------------------

# Some older code expects a row named "ipo_id" in the metadata table.
# This table is now documents.id, so we normalize on the application side.
