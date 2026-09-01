from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
from contextlib import asynccontextmanager
from pprint import pprint

from core.settings import settings
from core.startup import clean_startup
from core.runtime_state import set_current_ipo, get_current_ipo, reset_current_ipo
from core.supabase_client import (
    DatabaseConnectionError,
    DatabaseOperationError,
    get_document_by_hash,
    execute_supabase,
    supabase,
)

from rag.decision_report_generator import generate_decision_report, sanitize
from rag.investment_verdict import generate_investment_verdict
from rag.upload import list_all_ipos, delete_ipo_vectors, get_ipo_stats
from rag.ingestion import compute_document_hash, load_chunk_documents

from main import run, reset_chunks


# --------------------------------------------------
# App setup
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    clean_startup()
    yield


app = FastAPI(lifespan=lifespan)

app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "static"),
    name="static"
)


# --------------------------------------------------
# Static assets
# --------------------------------------------------

@app.get("/favicon.ico")
async def favicon():
    favicon_path = BASE_DIR / "static" / "favicon.ico"
    if not favicon_path.exists():
        raise HTTPException(status_code=404, detail="favicon not found")
    return FileResponse(favicon_path)


# --------------------------------------------------
# Health Check
# --------------------------------------------------

@app.get("/health")
def health_check():
    """Quick health check endpoint."""
    ipo_id = get_current_ipo()
    return {
        "status": "healthy",
        "ipo_loaded": ipo_id is not None,
        "current_ipo": ipo_id
    }


# --------------------------------------------------
# Models
# --------------------------------------------------

class QueryRequest(BaseModel):
    query: str


def resolve_active_document_id() -> str:
    """Resolve a canonical completed document ID for request-scoped retrieval."""
    current_id = get_current_ipo()
    if current_id:
        try:
            import uuid
            uuid.UUID(str(current_id))
            return str(current_id)
        except (ValueError, AttributeError, TypeError):
            pass

    result = execute_supabase(
        "resolve active completed document",
        supabase.table("documents")
        .select("id")
        .eq("processing_status", "completed"),
    )
    documents = result.data or []
    if not documents:
        raise ValueError("No completed document is available. Upload a DRHP PDF first using /upload")
    if len(documents) > 1:
        raise ValueError("Multiple completed documents are available. Select a document before asking a question.")

    document_id = documents[0].get("id")
    if not document_id:
        raise ValueError("The completed document record has no canonical document ID.")
    return str(document_id)


# --------------------------------------------------
# Frontend
# --------------------------------------------------

@app.get("/")
def serve_frontend():
    return FileResponse(BASE_DIR / "static" / "index.html")


# --------------------------------------------------
# Reset system
# --------------------------------------------------

@app.post("/reset")
def reset():
    clean_startup()
    reset_chunks()
    reset_current_ipo()
    return {"status": "reset complete"}


# --------------------------------------------------
# Upload DRHP PDF (Production-grade ingestion)
# --------------------------------------------------

@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    """
    Upload a DRHP PDF for analysis.
    
    - Performs semantic chunking (400 tokens per chunk)
    - Generates embeddings with batch processing
    - Supports large PDFs (1000+ pages)
    - Extracts metadata (section, page, document name)
    
    Returns:
        - ipo_id: Unique document identifier
        - chunks_created: Number of chunks extracted
        - processing_time: Ingestion duration
    """
    import time
    
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are allowed"
        )

    settings.docs_dir.mkdir(parents=True, exist_ok=True)

    save_path = settings.docs_dir / file.filename

    contents = await file.read()

    with open(save_path, "wb") as f:
        f.write(contents)

    ipo_id = file.filename.replace(".pdf", "").lower()

    print(f"\n[API] /upload: Processing {file.filename}")
    print(f"[API] IPO ID: {ipo_id}")
    
    try:
        # Run production ingestion pipeline
        # This will:
        # 1. Parse PDF with pdfplumber
        # 2. Extract sections using TOC parser
        # 3. Perform semantic chunking (400 tokens)
        # 4. Generate embeddings in batches
        # 5. Store in Supabase with full metadata
        
        start_time = time.time()
        chunks_count = load_chunk_documents()
        elapsed = time.time() - start_time

        document_result = get_document_by_hash(compute_document_hash(save_path))
        if not document_result.data:
            raise RuntimeError("Ingestion completed but the canonical document record could not be resolved.")
        ipo_id = str(document_result.data[0]["id"])
        set_current_ipo(ipo_id)
        reset_chunks()

        print(f"[API] /upload: SUCCESS")
        print(f"[API] Chunks: {chunks_count}, Time: {elapsed:.2f}s\n")

        return {
            "status": "uploaded",
            "ipo_id": ipo_id,
            "chunks_created": chunks_count,
            "processing_time_seconds": round(elapsed, 2),
            "message": f"PDF processed successfully with {chunks_count} semantic chunks"
        }
        
    except DatabaseConnectionError as e:
        print(f"[API] /upload: DATABASE_UNAVAILABLE - {e}\n")
        raise HTTPException(
            status_code=503,
            detail=str(e)
        )

    except DatabaseOperationError as e:
        print(f"[API] /upload: DATABASE_ERROR - {e}\n")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

    except Exception as e:
        print(f"[API] /upload: ERROR - {e}\n")
        raise HTTPException(
            status_code=500,
            detail=f"PDF processing failed: {str(e)}"
        )


# --------------------------------------------------
# Decision Report
# --------------------------------------------------

@app.post("/decision-report")
def decision_report():

    ipo_id = get_current_ipo()

    if not ipo_id:
        raise HTTPException(
            status_code=400,
            detail="No IPO uploaded. Upload a DRHP first."
        )

    report = generate_decision_report()
    verdict = generate_investment_verdict(report)

    pprint(report)

    return {
        "report": sanitize(report),
        "verdict": verdict
    }


# --------------------------------------------------
# RAG Question Answering (Production hybrid retrieval)
# --------------------------------------------------

@app.post("/ask")
def ask(req: QueryRequest):
    """
    Answer questions about the uploaded DRHP using hierarchical RAG.
    
    Pipeline:
    1. Section Retrieval: Identify relevant sections
    2. Chunk Retrieval: Get chunks from relevant sections
    3. Reranking: Cross-encoder scores chunks
    4. Answer Generation: LLM generates answer with citations
    
    Returns:
        - answer: Answer with inline citations
        - faithfulness: Citation confidence score
        - chunks_used: Number of chunks used
        - sources: Section, page, document references
    """
    try:
        ipo_id = resolve_active_document_id()
        set_current_ipo(ipo_id)

        print(f"\n[API] /ask: Query = '{req.query[:60]}'")
        print(f"[API] /ask: IPO ID = {ipo_id}")

        # Run hierarchical RAG pipeline from main.py
        answer, faithfulness = run(req.query)

        # Extract source information from the answer for display
        # (metadata is embedded in the answer text)
        sources = []

        print(f"[API] /ask: SUCCESS")
        print(f"[API] /ask: Faithfulness = {faithfulness:.2f}\n")

        return {
            "answer": answer,
            "faithfulness": faithfulness,
            "status": "success"
        }

    except ValueError as e:
        print(f"[API] /ask: USER_ERROR - {e}\n")
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )

    except DatabaseConnectionError as e:
        print(f"[API] /ask: DATABASE_UNAVAILABLE - {e}\n")
        raise HTTPException(
            status_code=503,
            detail=str(e)
        )

    except DatabaseOperationError as e:
        print(f"[API] /ask: DATABASE_ERROR - {e}\n")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

    except Exception as e:
        print(f"[API] /ask: SERVER_ERROR - {e}\n")
        raise HTTPException(
            status_code=500,
            detail=f"Question answering failed: {str(e)}"
        )


# --------------------------------------------------
# Admin APIs
# --------------------------------------------------

@app.get("/admin/ipos")
def list_ipos():
    ipos = list_all_ipos()
    return {"ipos": ipos}


@app.delete("/admin/ipo/{ipo_id}")
def delete_ipo(ipo_id: str):
    delete_ipo_vectors(ipo_id)
    return {"status": "deleted", "ipo_id": ipo_id}


@app.get("/admin/ipo/{ipo_id}/stats")
def ipo_stats(ipo_id: str):
    """
    Get statistics about indexed chunks for an IPO.
    
    Returns:
        - chunk_count: Total semantic chunks
        - sections: Unique document sections
        - total_tokens: Aggregate tokens
        - average_chunk_size: Mean tokens per chunk
        - page_coverage: Pages with indexed content
    """
    stats = get_ipo_stats(ipo_id)
    
    # Enhance stats with production pipeline insights
    return {
        "ipo_id": ipo_id,
        "chunks_indexed": stats.get("chunk_count", 0),
        "sections_covered": stats.get("sections", []),
        "metadata": {
            "chunk_count": stats.get("chunk_count", 0),
            "sections": stats.get("sections", []),
            "embedding_count": stats.get("embedding_count", 0)
        }
    }
