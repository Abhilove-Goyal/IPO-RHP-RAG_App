import sys
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag.ingestion import _extract_table_metadata, build_asset_chunk_entries


PDF_PATH = ROOT / "Dhoot Transmission Limited - AP_p.pdf"
DOCUMENT_ID = "cc59e2bb-a891-5162-b64a-0fa7ebb30362"


def main() -> None:
    with pdfplumber.open(PDF_PATH) as pdf:
        assets = _extract_table_metadata(pdf.pages[0], 1, "general")

    assert assets, "Page 1 should contain a table asset"
    table = assets[0]
    entries = build_asset_chunk_entries(
        document_id=DOCUMENT_ID,
        document_name=PDF_PATH.name,
        asset=table,
        chunk_index_start=1,
        source_asset_id="fixture-asset-id",
    )
    combined_text = " ".join(entry["chunk_text"] for entry in entries)
    for term in ("BC Asia Investments XV Limited", "14,000", "19,137,602"):
        assert term.lower() in combined_text.lower(), f"Missing page-1 table term: {term}"
    assert entries, "Page 1 table should produce a searchable chunk"
    assert all(entry["page_number"] == 1 for entry in entries)
    assert all(entry["metadata"]["source_type"] == "table" for entry in entries)
    assert all(entry["metadata"]["source_asset_id"] == "fixture-asset-id" for entry in entries)
    print("ASSET_CHUNK_COVERAGE_PASS", len(entries))


if __name__ == "__main__":
    main()
