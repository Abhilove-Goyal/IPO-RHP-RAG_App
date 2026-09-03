import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag.bm25_retriever import BM25Retriever
from rag.ingestion import _extract_table_metadata, build_asset_chunk_entries, split_asset_search_text
from rag.retriever import hybrid_search


DOCUMENT_ID = "local-dhoot-table-test"
TABLE_ASSET = {
    "asset_type": "table",
    "source_identifier": "page-1-table-1",
    "caption": "Details of the Offer to the Public",
    "page_number": 1,
    "section": "offer",
    "subsection": "offer structure",
    "headers": ["Type of Offer", "Fresh Issue Size", "Offer for Sale Size", "Total Offer Size"],
    "rows": [
        ["Fresh Issue and Offer for Sale", "Up to ₹14,000 million", "Up to 19,137,602 Equity Shares", "Up to ₹14,000 million"],
        ["Selling Shareholders", "BC ASIA INVESTMENTS XV LIMITED", "RAHUL RADHAVALLABH DHOOT", "19,137,602 Equity Shares"],
        ["Promoters", "Dhoot Transmission Limited promoters", "Promoter group", "Company ownership"],
    ],
    "metadata": {"table_id": "page-1-table-1"},
}

REQUIRED_PAGE_ONE_VALUES = (
    "BC ASIA INVESTMENTS XV LIMITED",
    "RAHUL RADHAVALLABH DHOOT",
    "₹14,000 million",
    "19,137,602 Equity Shares",
    "Fresh Issue",
    "Offer for Sale",
    "Total Offer",
)


def test_table_chunks_repeat_headers_and_keep_complete_rows():
    chunks = split_asset_search_text(TABLE_ASSET, max_tokens=28)

    assert len(chunks) == 3
    combined_text = "\n".join(chunks)
    for value in REQUIRED_PAGE_ONE_VALUES:
        assert value in combined_text, f"Missing exact page-1 table value: {value}"
    header = "Type of Offer | Fresh Issue Size | Offer for Sale Size | Total Offer Size"
    for chunk in chunks:
        assert "TABLE: Details of the Offer to the Public" in chunk
        assert "PAGE: 1" in chunk
        assert "SOURCE: page-1-table-1" in chunk
        assert f"HEADERS:\n{header}" in chunk
        assert "ROW:" in chunk

    assert "Selling Shareholders | BC ASIA INVESTMENTS XV LIMITED | RAHUL RADHAVALLABH DHOOT | 19,137,602 Equity Shares" in chunks[1]
    assert "Promoters | Dhoot Transmission Limited promoters | Promoter group | Company ownership" in chunks[2]
    assert all("ROW:\nBC Asia Investments XV Limited" not in chunk for chunk in chunks)

    entries = build_asset_chunk_entries(
        document_id=DOCUMENT_ID,
        document_name="dhoot.pdf",
        asset=TABLE_ASSET,
        chunk_index_start=1,
        source_asset_id="local-asset",
    )
    assert all(entry["source_type"] == "table" for entry in entries)
    assert all(entry["metadata"]["table_id"] == "page-1-table-1" for entry in entries)
    assert all(entry["metadata"]["source_asset_id"] == "local-asset" for entry in entries)


def test_requested_questions_retrieve_table_through_bm25_and_rrf():
    chunk_rows = build_asset_chunk_entries(
        document_id=DOCUMENT_ID,
        document_name="dhoot.pdf",
        asset=TABLE_ASSET,
        chunk_index_start=1,
        source_asset_id="local-asset",
    )
    rows = [dict(entry, id=f"chunk-{entry['chunk_index']}") for entry in chunk_rows]
    questions = [
        "Who are the selling shareholders?",
        "What is the OFS size?",
        "What is the fresh issue size?",
        "What is the total offer size?",
        "Who are the promoters of the company?",
    ]

    with patch("rag.bm25_retriever.execute_supabase", return_value=SimpleNamespace(data=rows)), patch(
        "rag.retriever.vector_search", return_value=[]
    ):
        for question in questions:
            bm25 = BM25Retriever().search(document_id=DOCUMENT_ID, query=question, top_k=3)
            fused = hybrid_search(question, DOCUMENT_ID, vector_top_k=3, bm25_top_k=3, fusion_top_k=3)
            assert bm25, question
            assert fused, question
            assert fused[0]["source_type"] == "table"
            assert fused[0]["metadata"]["table_id"] == "page-1-table-1"
            assert "HEADERS:" in fused[0]["chunk_text"]
            assert "ROW:" in fused[0]["chunk_text"]


def test_actual_local_dhoot_page_one_asset_values_survive_conversion():
    pdf_path = ROOT / "data" / "docs" / "Dhoot Transmission Limited - AP_p.pdf"
    if not pdf_path.exists():
        pytest.skip("Local Dhoot PDF fixture is not available")

    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        asset = _extract_table_metadata(pdf.pages[0], 1, "general")[0]
    entries = build_asset_chunk_entries(
        document_id=DOCUMENT_ID,
        document_name=pdf_path.name,
        asset=asset,
        chunk_index_start=1,
        source_asset_id="local-dhoot-asset",
    )
    combined_text = "\n".join(entry["chunk_text"] for entry in entries)
    for value in REQUIRED_PAGE_ONE_VALUES:
        assert value.casefold() in combined_text.casefold(), f"Missing local Dhoot value: {value}"
    assert "DETAILS OF THE OFFER TO THE PUBLIC" in combined_text
    assert "DETAILS OF THE OFFER FOR SALE" in combined_text
    assert all(entry["source_type"] == "table" for entry in entries)
    assert all("HEADERS:" in entry["chunk_text"] and "ROW:" in entry["chunk_text"] for entry in entries)
    assert all(entry["chunk_text"].strip() not in asset.get("rows", []) for entry in entries)
