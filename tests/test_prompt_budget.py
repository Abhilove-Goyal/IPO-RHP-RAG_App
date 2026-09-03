import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag.ingestion import build_asset_chunk_entries
from rag.prompt_builder import (
    build_prompt,
    estimate_prompt_tokens,
    format_evidence,
    select_context_chunks,
)


TABLE_CHUNK = build_asset_chunk_entries(
    document_id="local-document",
    document_name="dhoot.pdf",
    asset={
        "asset_type": "table",
        "source_identifier": "page-1-table-1-1",
        "caption": "Details of the Offer to the Public",
        "page_number": 1,
        "section": "offer",
        "subsection": "offer structure",
        "headers": ["Type of Offer", "Fresh Issue Size", "Offer for Sale Size", "Total Offer Size"],
        "rows": [["Fresh Issue and Offer for Sale", "Up to ₹14,000 million", "Up to 19,137,602 Equity Shares", "Up to ₹14,000 million"]],
        "metadata": {"table_id": "page-1-table-1"},
    },
    chunk_index_start=1,
)[0]


def text_chunk(text: str, index: int = 1) -> dict:
    return {
        "id": f"text-{index}",
        "chunk_text": text,
        "document_name": "dhoot.pdf",
        "page_number": index,
        "section": "general",
        "subsection": None,
        "source_type": "text",
        "metadata": {},
    }


def test_oversized_context_is_reduced_without_cutting_table_chunks():
    large_text = text_chunk("ordinary evidence " * 1200)
    selected = select_context_chunks("What is the offer size?", [large_text, TABLE_CHUNK], token_budget=500)

    assert TABLE_CHUNK in selected
    assert large_text not in selected
    table_evidence = format_evidence([TABLE_CHUNK])
    assert "HEADERS:" in TABLE_CHUNK["chunk_text"]
    assert "ROW:" in TABLE_CHUNK["chunk_text"]
    assert TABLE_CHUNK["chunk_text"] in table_evidence


def test_table_metadata_and_complete_content_are_preserved():
    selected = select_context_chunks("What is the fresh issue size?", [TABLE_CHUNK], token_budget=1500)
    evidence = format_evidence(selected)

    assert selected == [TABLE_CHUNK]
    for label in ("Document: dhoot.pdf", "Page: 1", "Section: offer", "Subsection: offer structure", "Source: table"):
        assert label in evidence
    assert "page-1-table-1-1" in evidence
    assert "Type of Offer" in evidence
    assert "Fresh Issue and Offer for Sale" in evidence
    assert "₹14,000 million" in evidence
    assert "19,137,602 Equity Shares" in evidence


def test_structured_table_rows_are_not_duplicated_in_prompt():
    structured_table = dict(TABLE_CHUNK)
    structured_table["chunk_text"] += "\n\nHEADERS:\nType | Amount\n\nROW:\nOffer for Sale | 19,137,602 Equity Shares"
    structured_table["metadata"] = {
        **structured_table["metadata"],
        "rows": [["Offer for Sale", "19,137,602 Equity Shares"]] * 100,
    }

    evidence = format_evidence([structured_table])

    assert "[Table rows:" not in evidence
    assert "Offer for Sale | 19,137,602 Equity Shares" in evidence


def test_small_context_remains_unchanged():
    chunks = [text_chunk("First evidence", 1), text_chunk("Second evidence", 2)]
    assert select_context_chunks("Explain the business", chunks, token_budget=1000) == chunks
    assert build_prompt("Explain the business", chunks).count("First evidence") == 1
    assert build_prompt("Explain the business", chunks).count("Second evidence") == 1


def test_final_prompt_evidence_stays_within_budget():
    chunks = [text_chunk(f"evidence block {index} " * 500, index) for index in range(1, 20)]
    selected = select_context_chunks("Summarize the filing", chunks, token_budget=4000)
    evidence = format_evidence(selected)

    assert selected
    assert estimate_prompt_tokens(evidence) <= 4000
    assert estimate_prompt_tokens(evidence) < estimate_prompt_tokens(format_evidence(chunks))
    prompt = build_prompt("Summarize the filing", chunks)
    evidence_start = prompt.index("REFERENCE MATERIALS:\n") + len("REFERENCE MATERIALS:\n")
    evidence_end = prompt.index("\n\nQUESTION TO ANSWER:", evidence_start)
    assert estimate_prompt_tokens(prompt[evidence_start:evidence_end]) <= 4000
    assert estimate_prompt_tokens(prompt) < 8000
