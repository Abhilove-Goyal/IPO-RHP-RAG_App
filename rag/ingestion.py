import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pdfplumber

from core.document_structure import set_toc
from core.r2_storage import asset_object_key, document_object_key, upload_bytes, upload_file
from core.settings import settings
from core.supabase_client import (
    DatabaseConnectionError,
    DatabaseOperationError,
    create_document,
    create_document_asset,
    get_document_by_hash,
    insert_document_chunk,
    insert_ipo,
)
from rag.jina_embeddings import embed_texts
from rag.toc_parser import extract_toc


# ---------------------------------------------------------------------------
# Embedding model / compatibility shim
# ---------------------------------------------------------------------------

def get_embed_model():
    return {"provider": "jina", "model": settings.jina_embedding_model, "dimension": settings.embedding_dimension}


def count_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(re.findall(r"\S+", text)))


# ---------------------------------------------------------------------------
# Section detection and cleaning
# ---------------------------------------------------------------------------

SECTION_PATTERNS = {
    "risk_factors": r"risk factors|principal risks|key risks|risk management",
    "business": r"our business|business overview|business model|operations",
    "financials": r"financial information|financial statements|management discussion|results of operations|consolidated financial",
    "management": r"our management|board of directors|corporate governance|management discussion",
    "legal": r"legal proceedings|litigation|legal matters|regulatory matters",
    "industry": r"industry overview|market overview",
    "offer": r"details of the offer|offer structure|offer and listing|public offer",
    "capital_structure": r"capital structure|share capital|equity share capital",
    "use_of_proceeds": r"use of proceeds|application of proceeds",
    "introduction": r"introduction|overview",
}


HEADER_FOOTER_PATTERNS = [
    r"^page\s+\d+$",
    r"^\d+$",
    r"^draft red herring prospectus$",
    r"^red herring prospectus$",
    r"^initial public offering$",
    r"^\.{5,}$",
]


def detect_section(text: str, fallback: str = "general") -> str:
    text_lower = (text or "").lower()
    for section, pattern in SECTION_PATTERNS.items():
        if re.search(pattern, text_lower):
            return section
    return fallback


def is_boilerplate(text: str) -> bool:
    if not text:
        return True
    cleaned = (text or "").lower()
    patterns = [
        r"table of contents",
        r"^page\s+\d+$",
        r"\.{5,}",
        r"draft red herring prospectus",
        r"red herring prospectus",
    ]
    return any(re.search(pattern, cleaned) for pattern in patterns)


def _clean_page_text(raw_text: str | None) -> str:
    if not raw_text:
        return ""
    text = raw_text.replace("\r", "\n")
    text = text.replace("\xa0", " ")
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines: List[str] = []
    for line in text.split("\n"):
        value = re.sub(r"\s+", " ", line).strip()
        if not value:
            continue
        if re.match(r"^(?:page\s+)?\d+$", value, flags=re.IGNORECASE):
            continue
        if any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in HEADER_FOOTER_PATTERNS):
            continue
        lines.append(value)
    return "\n\n".join(lines).strip()


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Semantic chunking: keep paragraphs together, preserve structure metadata
# ---------------------------------------------------------------------------

def _split_blocks(text: str) -> List[str]:
    blocks: List[str] = []
    paragraphs = re.split(r"\n\s*\n", text.strip())
    for paragraph in paragraphs:
        cleaned = _normalize_whitespace(paragraph)
        if cleaned:
            blocks.append(cleaned)
    return blocks


def recursive_chunk_text(text: str, chunk_size: int = 800, overlap: int = 120) -> List[str]:
    cleaned = _normalize_whitespace(text)
    if not cleaned:
        return []
    if count_tokens(cleaned) <= chunk_size:
        return [cleaned]

    blocks = _split_blocks(cleaned)
    if not blocks or len(blocks) == 1:
        return [cleaned]

    chunks: List[str] = []
    current = ""
    for block in blocks:
        candidate = f"{current} {block}".strip() if current else block
        if count_tokens(candidate) <= chunk_size:
            current = candidate
            continue

        if current:
            chunks.append(current)
            overlap_words = current.split()[-max(1, overlap // 8):]
            current = " ".join(overlap_words)

        if count_tokens(block) <= chunk_size:
            current = block
        else:
            sentences = re.split(r"(?<=[.!?])\s+", block)
            sentence_buffer = ""
            for sentence in sentences:
                combined = f"{sentence_buffer} {sentence}".strip() if sentence_buffer else sentence
                if count_tokens(combined) <= chunk_size:
                    sentence_buffer = combined
                else:
                    if sentence_buffer:
                        chunks.append(sentence_buffer)
                    sentence_buffer = sentence
            if sentence_buffer:
                current = sentence_buffer

    if current:
        chunks.append(current)

    return [chunk for chunk in chunks if chunk.strip()]


def split_into_semantic_chunks(text: str, chunk_size: int = 800, overlap: int = 120) -> List[str]:
    chunks: List[str] = []
    for block in _split_blocks(text):
        chunks.extend(recursive_chunk_text(block, chunk_size=chunk_size, overlap=overlap))
    return chunks


# ---------------------------------------------------------------------------
# Table and figure extraction helpers
# ---------------------------------------------------------------------------

def _table_to_search_text(table: List[List[Any]], *, caption: str | None = None, page_number: int | None = None) -> str:
    if not table:
        return ""

    rows = []
    for row in table:
        cleaned = [str(cell).strip() if cell is not None else "" for cell in row]
        cleaned = [cell for cell in cleaned if cell]
        if cleaned:
            rows.append(" | ".join(cleaned))

    if not rows:
        return ""

    text_parts = []
    if caption:
        text_parts.append(f"Table {page_number or ''}: {caption}")
    if rows:
        text_parts.append("Table rows: " + " ; ".join(rows[:10]))
    if len(rows) > 10:
        text_parts.append(f"... plus {len(rows) - 10} additional rows.")
    return " ".join(text_parts)


def _extract_table_metadata(page, page_number: int, section: str, subsection: str | None = None) -> List[Dict[str, Any]]:
    table_rows: List[Dict[str, Any]] = []
    tables = page.extract_tables() or []
    for idx, table in enumerate(tables):
        table_text = _table_to_search_text(table, caption=f"Table {idx + 1}", page_number=page_number)
        representative_row = [cell for row in table[:3] for cell in (row or []) if cell]
        table_rows.append({
            "asset_type": "table",
            "source_identifier": f"page-{page_number}-table-{idx + 1}",
            "caption": f"Table {idx + 1}",
            "search_text": table_text,
            "headers": table[0] if table and table[0] else [],
            "rows": table[1:10] if table else [],
            "representative_values": representative_row[:12],
            "page_number": page_number,
            "section": section,
            "subsection": subsection,
            "metadata": {
                "source_type": "table",
                "table_id": f"page-{page_number}-table-{idx + 1}",
                "section": section,
                "subsection": subsection,
                "page_number": page_number,
                "headers": table[0] if table and table[0] else [],
                "row_count": max(0, len(table) - 1),
            },
        })
    return table_rows


def _extract_figure_metadata(page, page_number: int, section: str, subsection: str | None = None) -> List[Dict[str, Any]]:
    page_text = _clean_page_text(page.extract_text() or "")
    figure_rows: List[Dict[str, Any]] = []
    for idx, image in enumerate(page.images or [], start=1):
        caption_match = re.search(r"(?:figure|chart|graph|illustration)\s*\d+[:\-]?\s*([^\n]+)", page_text, flags=re.IGNORECASE)
        caption = caption_match.group(0).strip() if caption_match else f"Figure/Chart {idx}"
        figure_rows.append({
            "asset_type": "chart" if re.search(r"(?:chart|graph|figure)", caption, flags=re.IGNORECASE) else "image",
            "source_identifier": f"page-{page_number}-asset-{idx}",
            "caption": caption,
            "search_text": caption,
            "page_number": page_number,
            "section": section,
            "subsection": subsection,
            "metadata": {
                "source_type": "image_or_figure",
                "image_index": idx,
                "page_number": page_number,
                "section": section,
                "subsection": subsection,
                "caption": caption,
                "bbox": [image.get("x0"), image.get("top"), image.get("x1"), image.get("bottom")],
                "width": image.get("x1") - image.get("x0"),
                "height": image.get("bottom") - image.get("top"),
                "is_figure_candidate": True,
            },
        })
    return figure_rows


# ---------------------------------------------------------------------------
# Document-level analysis for offline validation / observability
# ---------------------------------------------------------------------------

def compute_document_hash(file_path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(file_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def analyze_pdf_for_features(pdf_path: str | Path) -> Dict[str, Any]:
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    info: Dict[str, Any] = {
        "file_name": path.name,
        "page_count": 0,
        "page_text": [],
        "table_candidates": 0,
        "image_candidates": 0,
        "chart_candidates": 0,
        "warnings": [],
    }

    with pdfplumber.open(path) as pdf:
        info["page_count"] = len(pdf.pages)
        for page in pdf.pages:
            page_number = page.page_number
            raw_text = page.extract_text() or ""
            cleaned = _clean_page_text(raw_text)
            info["page_text"].append({"page_number": page_number, "text": cleaned})
            if page.extract_tables():
                info["table_candidates"] += len(page.extract_tables())
            if page.images:
                info["image_candidates"] += len(page.images)
            if re.search(r"(?:figure|chart|graph)\s*\d+", cleaned, flags=re.IGNORECASE):
                info["chart_candidates"] += 1

    return info


def build_document_chunks(pdf_path: str | Path) -> List[Dict[str, Any]]:
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    chunks: List[Dict[str, Any]] = []
    section = "general"
    chunk_index = 0

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            page_number = page.page_number
            raw_text = page.extract_text() or ""
            cleaned = _clean_page_text(raw_text)
            if not cleaned or is_boilerplate(cleaned):
                continue

            detected = detect_section(cleaned, fallback=section)
            if detected:
                section = detected

            page_tables = _extract_table_metadata(page, page_number, section)
            for table_asset in page_tables:
                chunks.append({
                    "text": table_asset["search_text"],
                    "metadata": {
                        "document_name": path.name,
                        "page_number": page_number,
                        "section": section,
                        "subsection": table_asset.get("subsection"),
                        "chunk_index": chunk_index,
                        "source_type": "table",
                        "source_identifier": table_asset["source_identifier"],
                        "asset_type": "table",
                        "caption": table_asset.get("caption"),
                    },
                })
                chunk_index += 1

            narrative_chunks = split_into_semantic_chunks(cleaned, chunk_size=settings.chunk_size, overlap=settings.chunk_overlap)
            for narrative in narrative_chunks:
                chunks.append({
                    "text": narrative,
                    "metadata": {
                        "document_name": path.name,
                        "page_number": page_number,
                        "section": section,
                        "subsection": None,
                        "chunk_index": chunk_index,
                        "source_type": "text",
                        "source_identifier": f"page-{page_number}-text-{chunk_index}",
                        "asset_type": None,
                    },
                })
                chunk_index += 1

            page_figures = _extract_figure_metadata(page, page_number, section)
            for figure_asset in page_figures:
                chunks.append({
                    "text": figure_asset["search_text"],
                    "metadata": {
                        "document_name": path.name,
                        "page_number": page_number,
                        "section": section,
                        "subsection": figure_asset.get("subsection"),
                        "chunk_index": chunk_index,
                        "source_type": "chart",
                        "source_identifier": figure_asset["source_identifier"],
                        "asset_type": figure_asset["asset_type"],
                        "caption": figure_asset.get("caption"),
                    },
                })
                chunk_index += 1

    return chunks


# ---------------------------------------------------------------------------
# Main ingestion flow
# ---------------------------------------------------------------------------

def _process_chunk_batch(chunk_buffer: List[Dict[str, Any]]) -> int:
    if not chunk_buffer:
        return 0

    texts = [entry["chunk_text"] for entry in chunk_buffer]
    try:
        embeddings = embed_texts(texts)
    except Exception as exc:  # pragma: no cover - defensive fallback
        raise RuntimeError(f"Embedding generation failed: {exc}") from exc

    for entry, embedding in zip(chunk_buffer, embeddings):
        if len(embedding) != settings.embedding_dimension:
            raise ValueError(f"Embedding dimension mismatch: expected {settings.embedding_dimension}, got {len(embedding)}.")

        metadata = dict(entry.get("metadata") or {})
        metadata.setdefault("document_name", entry.get("document_name"))
        metadata.setdefault("source_type", entry.get("source_type", "text"))

        insert_document_chunk(
            document_id=entry["document_id"],
            chunk_index=entry["chunk_index"],
            chunk_text=entry["chunk_text"],
            page_number=entry.get("page_number"),
            section=entry.get("section") or "general",
            subsection=entry.get("subsection"),
            chunk_tokens=entry.get("chunk_tokens") or count_tokens(entry["chunk_text"]),
            embedding=embedding,
            embedding_model=settings.jina_embedding_model,
            metadata=metadata,
        )

    return len(chunk_buffer)


def _persist_page_assets(document_id: str, document_name: str, page, section: str, page_number: int) -> None:
    assets = []
    assets.extend(_extract_table_metadata(page, page_number, section))
    assets.extend(_extract_figure_metadata(page, page_number, section))

    for asset in assets:
        asset_id = asset["source_identifier"]
        asset_storage_key = asset_object_key(
            document_id,
            asset_id,
            f"{asset_id}.json",
        )
        asset_payload = json.dumps(asset, ensure_ascii=True, default=str).encode("utf-8")
        upload_bytes(asset_payload, asset_storage_key, content_type="application/json")
        payload = {
            "document_id": document_id,
            "asset_type": asset["asset_type"],
            "page_number": page_number,
            "storage_key": asset_storage_key,
            "content_type": "application/json",
            "metadata": {
                **(asset.get("metadata") or {}),
                "document_name": document_name,
                "section": section,
                "search_text": asset.get("search_text"),
                "caption": asset.get("caption"),
            },
        }
        try:
            create_document_asset(**payload)
        except (DatabaseConnectionError, DatabaseOperationError):
            raise
        except Exception as exc:
            print(f"[INGEST] Failed to store asset metadata for {document_id} page {page_number}: {exc}")


def load_chunk_documents():
    docs_path = settings.docs_dir
    if not docs_path.exists():
        raise RuntimeError(f"Docs path {docs_path} does not exist")

    total_chunks = 0
    for file_name in sorted(os.listdir(docs_path)):
        if not file_name.lower().endswith(".pdf"):
            continue

        pdf_path = docs_path / file_name
        document_name = file_name
        file_hash = compute_document_hash(pdf_path)
        document_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"decision-analyst:{file_hash}"))
        existing = get_document_by_hash(file_hash)
        if existing and existing.data:
            print(f"[INGEST] Skipping duplicate PDF {file_name} (hash match for {existing.data[0].get('id')})")
            continue

        original_storage_key = document_object_key(document_id, document_name)
        upload_file(pdf_path, original_storage_key, content_type="application/pdf")

        try:
            create_document(
                document_id=document_id,
                company_name=Path(document_name).stem,
                document_name=document_name,
                document_hash=file_hash,
                storage_key=original_storage_key,
                page_count=0,
                processing_status="processing",
                processing_error=None,
            )
        except (DatabaseConnectionError, DatabaseOperationError):
            raise
        except Exception as exc:
            raise RuntimeError(f"Could not create document metadata: {exc}") from exc

        print(f"\n[INGEST] Processing PDF: {file_name} (document_id={document_id})")

        chunk_buffer: List[Dict[str, Any]] = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                page_total = len(pdf.pages)
                for page_number, page in enumerate(pdf.pages, start=1):
                    text = _clean_page_text(page.extract_text() or "")
                    if not text or is_boilerplate(text):
                        _persist_page_assets(document_id, document_name, page, "general", page_number)
                        continue

                    section = detect_section(text, fallback="general")
                    _persist_page_assets(document_id, document_name, page, section, page_number)

                    narrative_chunks = split_into_semantic_chunks(text, chunk_size=settings.chunk_size, overlap=settings.chunk_overlap)
                    for chunk_index, chunk_text in enumerate(narrative_chunks):
                        chunk_buffer.append({
                            "document_id": document_id,
                            "document_name": document_name,
                            "chunk_index": len(chunk_buffer) + 1,
                            "chunk_text": chunk_text,
                            "page_number": page_number,
                            "section": section,
                            "subsection": None,
                            "source_type": "text",
                            "metadata": {
                                "document_name": document_name,
                                "page_number": page_number,
                                "section": section,
                                "subsection": None,
                                "source_type": "text",
                                "chunk_index": len(chunk_buffer) + 1,
                            },
                            "chunk_tokens": count_tokens(chunk_text),
                        })

                    for table_asset in _extract_table_metadata(page, page_number, section):
                        table_chunk_text = table_asset["search_text"]
                        if not table_chunk_text:
                            continue
                        chunk_buffer.append({
                            "document_id": document_id,
                            "document_name": document_name,
                            "chunk_index": len(chunk_buffer) + 1,
                            "chunk_text": table_chunk_text,
                            "page_number": page_number,
                            "section": section,
                            "subsection": table_asset.get("subsection"),
                            "source_type": "table",
                            "metadata": {
                                "document_name": document_name,
                                "page_number": page_number,
                                "section": section,
                                "subsection": table_asset.get("subsection"),
                                "source_type": "table",
                                "source_identifier": table_asset["source_identifier"],
                                "asset_type": "table",
                                "caption": table_asset.get("caption"),
                            },
                            "chunk_tokens": count_tokens(table_chunk_text),
                        })

                    for figure_asset in _extract_figure_metadata(page, page_number, section):
                        figure_text = figure_asset["search_text"]
                        if not figure_text:
                            continue
                        chunk_buffer.append({
                            "document_id": document_id,
                            "document_name": document_name,
                            "chunk_index": len(chunk_buffer) + 1,
                            "chunk_text": figure_text,
                            "page_number": page_number,
                            "section": section,
                            "subsection": figure_asset.get("subsection"),
                            "source_type": "chart",
                            "metadata": {
                                "document_name": document_name,
                                "page_number": page_number,
                                "section": section,
                                "subsection": figure_asset.get("subsection"),
                                "source_type": figure_asset["asset_type"],
                                "source_identifier": figure_asset["source_identifier"],
                                "asset_type": figure_asset["asset_type"],
                                "caption": figure_asset.get("caption"),
                            },
                            "chunk_tokens": count_tokens(figure_text),
                        })

                if page_total:
                    try:
                        create_document(
                            document_id=document_id,
                            company_name=Path(document_name).stem,
                            document_name=document_name,
                            document_hash=file_hash,
                            storage_key=original_storage_key,
                            page_count=page_total,
                            processing_status="completed",
                            processing_error=None,
                        )
                    except Exception:
                        pass

        except Exception as exc:
            try:
                create_document(
                    document_id=document_id,
                    company_name=Path(document_name).stem,
                    document_name=document_name,
                    document_hash=file_hash,
                    storage_key=original_storage_key,
                    page_count=0,
                    processing_status="failed",
                    processing_error=str(exc)[:500],
                )
            except Exception:
                pass
            raise RuntimeError(f"PDF processing failed for {file_name}: {exc}") from exc

        if chunk_buffer:
            total_chunks += _process_chunk_batch(chunk_buffer)

    return total_chunks
