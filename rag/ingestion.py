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
    list_document_assets,
    list_document_chunks,
    list_documents,
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


def _extract_narrative_text(page) -> str:
    """Extract page text while excluding words inside detected table bounds."""
    tables = page.find_tables() or []
    if not tables:
        return _clean_page_text(page.extract_text() or "")

    table_bounds = [table.bbox for table in tables]
    words = page.extract_words(use_text_flow=True) or []
    outside_words = []
    for word in words:
        x = (float(word.get("x0", 0)) + float(word.get("x1", 0))) / 2
        y = (float(word.get("top", 0)) + float(word.get("bottom", 0))) / 2
        if any(x0 <= x <= x1 and top <= y <= bottom for x0, top, x1, bottom in table_bounds):
            continue
        outside_words.append(word)

    lines: Dict[float, List[str]] = {}
    for word in outside_words:
        top = round(float(word.get("top", 0)), 1)
        lines.setdefault(top, []).append(str(word.get("text", "")))
    text = "\n".join(" ".join(parts) for _, parts in sorted(lines.items()))
    return _clean_page_text(text)


def split_asset_search_text(asset: Dict[str, Any], max_tokens: int | None = None) -> List[str]:
    """Split an asset search representation without separating table rows."""
    if asset.get("asset_type") == "table":
        return _table_chunk_texts(asset, max_tokens=max_tokens)

    search_text = _normalize_whitespace(asset.get("search_text") or "")
    if not search_text:
        return []
    max_tokens = max_tokens or settings.chunk_size
    if count_tokens(search_text) <= max_tokens:
        return [search_text]

    prefix, separator, rows_text = search_text.partition("Table rows: ")
    if not separator:
        return [search_text]

    row_groups = [row.strip() for row in rows_text.split(" ; ") if row.strip()]
    chunks: List[str] = []
    current = prefix.strip()
    for row in row_groups:
        candidate = f"{current} Table rows: {row}" if current else row
        if current and count_tokens(candidate) > max_tokens:
            chunks.append(current)
            current = f"{prefix.strip()} Table rows: {row}".strip()
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [search_text]


def _table_cell_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"₹\s+", "₹", _normalize_whitespace(str(value)))


def _table_row_text(row: Iterable[Any]) -> str:
    return " | ".join(_table_cell_text(cell) for cell in row)


def _is_table_section_label(text: str) -> bool:
    normalized = text.strip().upper()
    return normalized.startswith((
        "THE PROMOTERS OF OUR COMPANY",
        "DETAILS OF THE OFFER",
    ))


def _is_table_header_fragment(cells: List[str]) -> bool:
    if not cells:
        return False
    joined = " ".join(cells)
    if len(joined) > 100 or re.search(r"\d|₹|up to|equity shares|million", joined, flags=re.IGNORECASE):
        return False
    return all(len(cell) <= 40 for cell in cells)


def _logical_table_rows(rows: Iterable[Iterable[Any]]) -> List[str]:
    """Fold PDF line-wrap fragments into searchable, contextual table rows."""
    logical_rows: List[str] = []
    section_context: List[str] = []
    header_fragments: List[str] = []

    for raw_row in rows:
        cells = [_table_cell_text(cell) for cell in raw_row]
        cells = [cell for cell in cells if cell]
        if not cells:
            continue

        row_text = _table_row_text(cells)
        if _is_table_section_label(row_text):
            if row_text not in section_context:
                section_context.append(row_text)
            continue

        if _is_table_header_fragment(cells):
            header_fragments.extend(cells)
            continue

        context_lines = list(section_context)
        if header_fragments:
            context_lines.append("COLUMN CONTEXT: " + _table_row_text(header_fragments))
            header_fragments = []
        context = "\n".join(f"TABLE CONTEXT: {line}" for line in context_lines)
        logical_rows.append(f"{context}\nROW DATA: {row_text}" if context else row_text)

    if header_fragments:
        context_lines = list(section_context)
        context_lines.append("COLUMN CONTEXT: " + _table_row_text(header_fragments))
        logical_rows.append("\n".join(f"TABLE CONTEXT: {line}" for line in context_lines))
    return logical_rows


def _table_chunk_texts(asset: Dict[str, Any], max_tokens: int | None = None) -> List[str]:
    """Render self-contained table chunks, keeping headers with complete rows."""
    headers = list(asset.get("headers") or [])
    rows = [list(row or []) for row in (asset.get("rows") or [])]
    if not headers and rows:
        headers = rows.pop(0)
    if not headers:
        return []

    asset_metadata = asset.get("metadata") or {}
    caption = (
        asset.get("caption")
        or asset.get("title")
        or asset_metadata.get("caption")
        or asset_metadata.get("title")
        or "Untitled table"
    )
    page_number = asset.get("page_number") or asset_metadata.get("page_number")
    source_identifier = asset.get("source_identifier") or asset_metadata.get("source_identifier") or asset_metadata.get("table_id")
    section = asset.get("section") or asset_metadata.get("section")
    subsection = asset.get("subsection") or asset_metadata.get("subsection")

    prefix = [f"TABLE: {_table_cell_text(caption)}"]
    if page_number is not None:
        prefix.append(f"PAGE: {page_number}")
    if source_identifier:
        prefix.append(f"SOURCE: {_table_cell_text(source_identifier)}")
    if section:
        prefix.append(f"SECTION: {_table_cell_text(section)}")
    if subsection:
        prefix.append(f"SUBSECTION: {_table_cell_text(subsection)}")
    header_text = _table_row_text(headers)
    raw_table_text = " ".join(_table_row_text(row) for row in rows)
    searchable_text = f"{header_text} {raw_table_text}"
    searchable_terms = []
    if re.search(r"fresh issue", searchable_text, flags=re.IGNORECASE):
        searchable_terms.append("Fresh Issue")
    if re.search(r"offer for sale", searchable_text, flags=re.IGNORECASE):
        searchable_terms.append("Offer for Sale")
    if re.search(r"total", searchable_text, flags=re.IGNORECASE) and re.search(r"offer", searchable_text, flags=re.IGNORECASE):
        searchable_terms.append("Total Offer")
    terms_text = f"\nKEY TERMS: {' | '.join(searchable_terms)}" if searchable_terms else ""
    prefix_text = "\n".join(prefix) + "\nSOURCE_TYPE: table\n\nHEADERS:\n" + header_text + terms_text
    row_blocks = [f"ROW:\n{row}" for row in _logical_table_rows(rows)]
    if not row_blocks:
        return [prefix_text]

    max_tokens = max_tokens or settings.chunk_size
    chunks: List[str] = []
    current = prefix_text
    for row_block in row_blocks:
        candidate = f"{current}\n\n{row_block}"
        if current != prefix_text and count_tokens(candidate) > max_tokens:
            chunks.append(current)
            current = f"{prefix_text}\n\n{row_block}"
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def build_asset_chunk_entries(
    *,
    document_id: str,
    document_name: str,
    asset: Dict[str, Any],
    chunk_index_start: int,
    source_asset_id: str | None = None,
) -> List[Dict[str, Any]]:
    """Build searchable chunk rows for one persisted asset."""
    asset_identifier = asset["source_identifier"]
    text_parts = split_asset_search_text(asset)
    entries = []
    for offset, text in enumerate(text_parts, start=1):
        identifier = asset_identifier if len(text_parts) == 1 else f"{asset_identifier}-{offset}"
        asset_metadata = asset.get("metadata") or {}
        metadata = {
            "document_name": document_name,
            "page_number": asset.get("page_number"),
            "section": asset.get("section") or "general",
            "subsection": asset.get("subsection"),
            "source_type": asset.get("asset_type") or "asset",
            "source_identifier": identifier,
            "parent_source_identifier": asset_identifier,
            "source_asset_id": source_asset_id,
            "asset_type": asset.get("asset_type"),
            "caption": asset.get("caption"),
        }
        if asset.get("asset_type") == "table":
            metadata.update({
                "headers": asset.get("headers") or [],
                "rows": asset.get("rows") or [],
                "representative_values": asset.get("representative_values") or [],
                "table_id": asset.get("table_id") or asset_metadata.get("table_id") or asset_identifier,
            })
        entries.append({
            "document_id": document_id,
            "document_name": document_name,
            "chunk_index": chunk_index_start + offset - 1,
            "chunk_text": text,
            "page_number": asset.get("page_number"),
            "section": asset.get("section") or "general",
            "subsection": asset.get("subsection"),
            "source_type": asset.get("asset_type") or "asset",
            "metadata": metadata,
            "chunk_tokens": count_tokens(text),
        })
    return entries


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
            "table_id": f"page-{page_number}-table-{idx + 1}",
            "caption": f"Table {idx + 1}",
            "search_text": table_text,
            "headers": table[0] if table and table[0] else [],
            "rows": table[1:] if table else [],
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
                for table_chunk in split_asset_search_text(table_asset):
                    chunks.append({
                        "text": table_chunk,
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
                            "table_id": table_asset.get("table_id") or table_asset["source_identifier"],
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


def _persist_page_assets(document_id: str, document_name: str, page, section: str, page_number: int) -> List[Dict[str, Any]]:
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
                "source_identifier": asset["source_identifier"],
                "search_text": asset.get("search_text"),
                "caption": asset.get("caption"),
            },
        }
        try:
            response = create_document_asset(**payload)
            asset_id = None
            if getattr(response, "data", None):
                asset_id = response.data[0].get("id")
            asset["source_asset_id"] = asset_id
        except (DatabaseConnectionError, DatabaseOperationError):
            raise
        except Exception as exc:
            print(f"[INGEST] Failed to store asset metadata for {document_id} page {page_number}: {exc}")

    return assets


def backfill_missing_asset_chunks() -> int:
    """Backfill searchable chunks for persisted R2-backed document assets."""
    inserted = 0
    documents = list_documents().data or []
    for document in documents:
        document_id = str(document.get("id"))
        document_name = document.get("document_name") or document_id
        existing_rows = list_document_chunks(document_id, limit=1000).data or []
        existing_identifiers = set()
        for row in existing_rows:
            metadata = row.get("metadata") or {}
            for key in ("source_identifier", "parent_source_identifier"):
                if metadata.get(key):
                    existing_identifiers.add(metadata[key])
        next_chunk_index = max((int(row.get("chunk_index") or 0) for row in existing_rows), default=0) + 1

        assets = list_document_assets(document_id, limit=1000).data or []
        for asset_row in assets:
            metadata = asset_row.get("metadata") or {}
            storage_key = asset_row.get("storage_key")
            if not storage_key:
                continue
            from core.r2_storage import download_bytes
            asset = json.loads(download_bytes(storage_key).decode("utf-8"))
            source_identifier = metadata.get("source_identifier") or asset.get("source_identifier")
            if not source_identifier or source_identifier in existing_identifiers:
                continue
            asset.update({
                "source_identifier": source_identifier,
                "page_number": asset_row.get("page_number"),
                "section": metadata.get("section") or asset.get("section"),
                "subsection": metadata.get("subsection") or asset.get("subsection"),
            })
            entries = build_asset_chunk_entries(
                document_id=document_id,
                document_name=document_name,
                asset=asset,
                chunk_index_start=next_chunk_index,
                source_asset_id=asset_row.get("id"),
            )
            _process_chunk_batch(entries)
            inserted += len(entries)
            next_chunk_index += len(entries)
            existing_identifiers.add(source_identifier)
    return inserted


def _assert_page_asset_chunk_invariant(document_id: str, page_number: int, assets: List[Dict[str, Any]]) -> None:
    if not assets:
        return

    chunk_response = list_document_chunks(document_id, limit=1000, start_page=page_number, end_page=page_number)
    chunks = chunk_response.data or []
    identifiers = [
        (chunk.get("metadata") or {}).get("source_identifier")
        for chunk in chunks
    ]
    parent_identifiers = [
        (chunk.get("metadata") or {}).get("parent_source_identifier")
        for chunk in chunks
    ]
    missing = []
    for asset in assets:
        source_identifier = asset.get("source_identifier")
        if source_identifier not in identifiers and source_identifier not in parent_identifiers:
            missing.append(source_identifier)
    if missing:
        raise RuntimeError(
            f"Asset chunk invariant failed for document {document_id} page {page_number}: "
            f"missing searchable chunks for {', '.join(missing)}"
        )


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

        next_chunk_index = 1
        try:
            with pdfplumber.open(pdf_path) as pdf:
                page_total = len(pdf.pages)
                for page_number, page in enumerate(pdf.pages, start=1):
                    text = _clean_page_text(page.extract_text() or "")
                    section = detect_section(text, fallback="general") if text else "general"
                    page_assets = _persist_page_assets(document_id, document_name, page, section, page_number)
                    page_buffer: List[Dict[str, Any]] = []

                    narrative_text = _extract_narrative_text(page)
                    if narrative_text and not is_boilerplate(narrative_text):
                        narrative_chunks = split_into_semantic_chunks(narrative_text, chunk_size=settings.chunk_size, overlap=settings.chunk_overlap)
                        for chunk_text in narrative_chunks:
                            page_buffer.append({
                                "document_id": document_id,
                                "document_name": document_name,
                                "chunk_index": next_chunk_index + len(page_buffer),
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
                                    "chunk_index": next_chunk_index + len(page_buffer),
                                },
                                "chunk_tokens": count_tokens(chunk_text),
                            })

                    for asset in page_assets:
                        page_buffer.extend(build_asset_chunk_entries(
                            document_id=document_id,
                            document_name=document_name,
                            asset=asset,
                            chunk_index_start=next_chunk_index + len(page_buffer),
                            source_asset_id=asset.get("source_asset_id"),
                        ))

                    if page_buffer:
                        _process_chunk_batch(page_buffer)
                        total_chunks += len(page_buffer)
                        next_chunk_index += len(page_buffer)
                    _assert_page_asset_chunk_invariant(document_id, page_number, page_assets)

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

    return total_chunks
