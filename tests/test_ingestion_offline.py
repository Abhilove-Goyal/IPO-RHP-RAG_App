import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag.ingestion import analyze_pdf_for_features, build_document_chunks


def main() -> None:
    candidates = [
        Path("groww.pdf"),
        Path("shadowfax.pdf"),
        Path("BCCL.pdf"),
        Path("IPO_DRHP_Checklist.pdf"),
    ]
    pdf_path = next((p for p in candidates if p.exists()), None)
    if pdf_path is None:
        raise FileNotFoundError("No representative PDF was found in the repository root.")

    features = analyze_pdf_for_features(pdf_path)
    assert features["page_count"] > 0, "PDF should have at least one page"
    assert "page_text" in features, "Feature analysis should include extracted text"

    chunks = build_document_chunks(pdf_path)
    assert chunks, "The sample PDF should yield at least one chunk"
    assert all("page_number" in chunk["metadata"] for chunk in chunks), "Each chunk should preserve page metadata"
    assert all("section" in chunk["metadata"] for chunk in chunks), "Each chunk should preserve section metadata"

    print(f"Validated PDF: {pdf_path.name}")
    print(f"Pages: {features['page_count']}")
    print(f"Text blocks: {len(features.get('page_text', []))}")
    print(f"Table candidates: {features.get('table_candidates', 0)}")
    print(f"Image candidates: {features.get('image_candidates', 0)}")
    print(f"Chunks: {len(chunks)}")


if __name__ == "__main__":
    main()
