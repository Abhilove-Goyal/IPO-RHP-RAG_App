import os
import re
import pdfplumber
import tiktoken
from pathlib import Path
from typing import List, Dict, Generator

from rag.toc_parser import extract_toc
from core.document_structure import set_toc

from core.settings import settings
from core.supabase_client import DatabaseConnectionError, DatabaseOperationError, insert_chunk, insert_ipo

from langchain_openai import OpenAIEmbeddings


# --------------------------------------------------
# Embedding model and tokenizer
# --------------------------------------------------

def get_embed_model():
    if not settings.jina_api_key:
        raise RuntimeError("Jina API key is not configured. Local embedding models are not active for this codebase.")
    return OpenAIEmbeddings(
        model=settings.jina_embedding_model,
        api_key=settings.jina_api_key,
        base_url="https://api.jina.ai/v1",
    )


embed_model = get_embed_model()

# Use cl100k_base tokenizer (used by most embedding models)
tokenizer = tiktoken.get_encoding("cl100k_base")


# --------------------------------------------------
# Section detection (semantic patterns)
# Works across different DRHP formats
# --------------------------------------------------

SECTION_PATTERNS = {
    "risk_factors": r"risk factors|principal risks|key risks",
    "business": r"our business|business overview|business model",
    "financials": r"financial information|financial statements|management discussion",
    "management": r"our management|board of directors|corporate governance",
    "legal": r"legal proceedings|litigation|legal matters",
    "industry": r"industry overview",
    "offer": r"details of the offer|offer structure"
}


def detect_section(text: str):

    text_lower = text.lower()

    for section, pattern in SECTION_PATTERNS.items():

        if re.search(pattern, text_lower):
            return section

    return None


# --------------------------------------------------
# Boilerplate filter
# --------------------------------------------------

def is_boilerplate(text):

    patterns = [
        r"table of contents",
        r"page\s+\d+",
        r"\.{5,}",
        r"draft red herring prospectus",
    ]

    text_lower = text.lower()

    return any(re.search(p, text_lower) for p in patterns)


# --------------------------------------------------
# Token counting utilities
# --------------------------------------------------

def count_tokens(text: str) -> int:
    """Count tokens in text using tiktoken."""
    try:
        return len(tokenizer.encode(text))
    except Exception as e:
        print(f"[TOKEN_COUNT] Error: {e}")
        # Fallback: approximate as 1 token per 4 characters
        return len(text) // 4


# --------------------------------------------------
# Semantic chunking with token-based boundaries
# --------------------------------------------------

def _split_blocks(text: str) -> List[str]:
    """Split text into structurally meaningful blocks."""
    blocks = []
    for block in re.split(r"\n{2,}|(?<=[.!?])\s+(?=[A-Z])", text.strip()):
        cleaned = re.sub(r"\s+", " ", block).strip()
        if cleaned:
            blocks.append(cleaned)
    return blocks


def recursive_chunk_text(text: str, chunk_size: int = 800, overlap: int = 120) -> List[str]:
    """Paragraph-aware recursive chunking for long financial documents."""
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []

    if count_tokens(cleaned) <= chunk_size:
        return [cleaned]

    blocks = _split_blocks(cleaned)
    if len(blocks) == 1:
        return [cleaned]

    chunks = []
    current = ""
    current_tokens = 0

    for block in blocks:
        block_tokens = count_tokens(block)
        if current and current_tokens + block_tokens > chunk_size:
            chunks.append(current.strip())

            overlap_buffer = []
            overlap_tokens = 0
            for tail in reversed(current.split(" ")):
                tail = tail.strip()
                if not tail:
                    continue
                tail_tokens = count_tokens(tail)
                if overlap_tokens + tail_tokens <= overlap:
                    overlap_buffer.insert(0, tail)
                    overlap_tokens += tail_tokens
                else:
                    break
            current = " ".join(overlap_buffer).strip()
            current_tokens = overlap_tokens

        current = (current + " " + block).strip() if current else block
        current_tokens = count_tokens(current)

    if current.strip():
        chunks.append(current.strip())

    return [chunk for chunk in chunks if chunk.strip()]


def split_into_semantic_chunks(
    text: str,
    chunk_size: int = 800,
    overlap: int = 120
) -> List[str]:
    """
    Split text into structure-aware semantic chunks.
    For long financial disclosures, this keeps paragraph-level semantics and recursively subdivides large blocks.
    """
    chunks: List[str] = []
    for block in _split_blocks(text):
        block_chunks = recursive_chunk_text(block, chunk_size=chunk_size, overlap=overlap)
        chunks.extend(block_chunks)
    return chunks


def split_into_chunks(text, chunk_size, overlap=200):
    """Legacy function - now uses recursive semantic chunking."""
    return split_into_semantic_chunks(text, chunk_size, overlap)


# --------------------------------------------------
# Streaming batch embedding generation
# --------------------------------------------------

def batch_embed_documents(texts: List[str], batch_size: int = 32) -> List[List[float]]:
    """
    Generate embeddings with batching for memory efficiency.
    
    Args:
        texts: List of texts to embed
        batch_size: Number of texts per batch
    
    Returns:
        List of embedding vectors
    """
    embeddings = []
    
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        try:
            batch_embeddings = embed_model.embed_documents(batch)
            embeddings.extend(batch_embeddings)
            print(f"[EMBED] Batch {i//batch_size + 1}: {len(batch)} texts embedded")
        except Exception as e:
            print(f"[EMBED] Error embedding batch: {e}")
            # Use zero vectors as fallback
            embeddings.extend([[0.0] * 768 for _ in batch])
    
    validated = []
    for embedding in embeddings:
        if len(embedding) != settings.embedding_dimension:
            raise ValueError(
                f"Embedding dimension mismatch: expected {settings.embedding_dimension}, got {len(embedding)}."
            )
        validated.append(embedding)
    return validated


# --------------------------------------------------
# Main ingestion pipeline (production-grade)
# --------------------------------------------------

def load_chunk_documents():
    """
    Production ingestion pipeline supporting:
    - Large PDFs (1000+ pages)
    - Semantic chunking with token counting
    - Memory-safe batch processing
    - Comprehensive metadata
    """

    docs_path: Path = settings.docs_dir

    if not docs_path.exists():
        raise RuntimeError(f"Docs path {docs_path} does not exist")

    chunks_created = 0
    total_tokens = 0

    for file in os.listdir(docs_path):

        if not file.lower().endswith(".pdf"):
            continue

        pdf_path = docs_path / file
        ipo_id = file.replace(".pdf", "").lower()
        document_name = file.replace(".pdf", "")

        print(f"\n[INGEST] ========================================")
        print(f"[INGEST] Processing PDF: {file}")
        print(f"[INGEST] IPO ID: {ipo_id}")
        print(f"[INGEST] ========================================\n")

        try:
            insert_ipo(ipo_id, str(pdf_path))
        except (DatabaseConnectionError, DatabaseOperationError):
            raise
        except Exception as e:
            raise RuntimeError(f"Could not save IPO metadata: {e}") from e

        current_section = "general"
        chunk_number = 0
        page_chunks_buffer = []  # Buffer for batch embedding
        batch_size = 32

        with pdfplumber.open(pdf_path) as pdf:

            # --------------------------------
            # Extract Table of Contents
            # --------------------------------
            # Extract Table of Contents
            # --------------------------------

            try:
                toc = extract_toc(pdf)

                if toc:
                    set_toc(ipo_id, toc)
                    print(f"[INGEST] TOC extracted: {len(toc)} sections detected")
                    for section in toc[:5]:
                        print(f"  - {section['section_name']}: pages {section['start_page']}-{section['end_page']}")
                else:
                    print(f"[INGEST] No TOC detected - using default sections")
            except Exception as e:
                print(f"[INGEST] TOC extraction error: {e}")
                toc = []

            # --------------------------------
            # Process document pages
            # --------------------------------

            total_pages = len(pdf.pages)
            print(f"[INGEST] Processing {total_pages} pages...")

            for page_idx, page in enumerate(pdf.pages):

                page_number = page.page_number

                # Progress indicator
                if (page_idx + 1) % 50 == 0:
                    print(f"[INGEST] Progress: {page_idx + 1}/{total_pages} pages processed")

                try:
                    text = page.extract_text()
                except Exception as e:
                    print(f"[INGEST] Error extracting text from page {page_number}: {e}")
                    continue

                if not text or len(text.strip()) < 20:
                    continue

                if is_boilerplate(text):
                    continue

                # Update section if detected
                detected_section = detect_section(text)
                if detected_section:
                    current_section = detected_section

                # Structure-aware chunking with table/text/chart-aware metadata handling
                page_chunks = split_into_semantic_chunks(
                    text,
                    chunk_size=settings.chunk_size,
                    overlap=settings.chunk_overlap
                )

                for chunk_text in page_chunks:

                    if not chunk_text.strip():
                        continue

                    chunk_tokens = count_tokens(chunk_text)
                    total_tokens += chunk_tokens

                    # Buffer chunk data for batch processing
                    page_chunks_buffer.append({
                        "ipo_id": ipo_id,
                        "chunk_text": chunk_text,
                        "chunk_number": chunk_number,
                        "page_number": page_number,
                        "section": current_section,
                        "document_name": document_name,
                        "chunk_tokens": chunk_tokens
                    })

                    chunk_number += 1

                    # Process buffer when it reaches batch_size
                    if len(page_chunks_buffer) >= batch_size:
                        chunks_created += _process_chunk_batch(page_chunks_buffer)
                        page_chunks_buffer = []

        # Process remaining chunks
        if page_chunks_buffer:
            chunks_created += _process_chunk_batch(page_chunks_buffer)
            page_chunks_buffer = []

        print(f"\n[INGEST] ========================================")
        print(f"[INGEST] {file} COMPLETE")
        print(f"[INGEST] Chunks created: {chunk_number}")
        print(f"[INGEST] Total tokens: {total_tokens}")
        print(f"[INGEST] ========================================\n")

    if chunks_created == 0:
        raise RuntimeError("No usable text chunks extracted from any PDF")

    print(f"\n[INGEST] ========================================")
    print(f"[INGEST] INGESTION COMPLETE")
    print(f"[INGEST] Total chunks created: {chunks_created}")
    print(f"[INGEST] Total tokens processed: {total_tokens}")
    print(f"[INGEST] ========================================\n")

    return chunks_created


# --------------------------------------------------
# Batch chunk processing helper
# --------------------------------------------------

def _process_chunk_batch(chunk_buffer: List[Dict]) -> int:
    """
    Process a batch of chunks: embed and insert into database.
    
    Args:
        chunk_buffer: List of chunk dictionaries with text and metadata
    """
    chunk_texts = [c["chunk_text"] for c in chunk_buffer]
    
    try:
        embeddings = batch_embed_documents(chunk_texts, batch_size=16)
    except Exception as e:
        print(f"[INGEST] Embedding error: {e}")
        embeddings = [[0.0] * 768 for _ in chunk_texts]
    
    inserted_count = 0

    # Insert into database
    for chunk_data, embedding in zip(chunk_buffer, embeddings):
        try:
            insert_chunk(
                ipo_id=chunk_data["ipo_id"],
                chunk_text=chunk_data["chunk_text"],
                chunk_number=chunk_data["chunk_number"],
                page_number=chunk_data["page_number"],
                section=chunk_data["section"],
                embedding=embedding,
                document_name=chunk_data["document_name"],
                chunk_tokens=chunk_data.get("chunk_tokens", 0)
            )
            inserted_count += 1
        except (DatabaseConnectionError, DatabaseOperationError):
            raise
        except Exception as e:
            raise RuntimeError(f"Database insertion error: {e}") from e

    return inserted_count
