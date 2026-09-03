"""Guarded Dhoot cleanup/re-ingestion utility.

Default mode is read-only. The destructive sequence requires --apply and is
intentionally not executed by this project workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.r2_storage import delete_object, document_object_key, object_exists
from core.supabase_client import execute_supabase, supabase
from rag.ingestion import compute_document_hash, load_chunk_documents

DOCUMENT_ID = "cc59e2bb-a891-5162-b64a-0fa7ebb30362"
DOCUMENT_NAME = "Dhoot Transmission Limited - AP_p.pdf"
DOCS_PATH = Path("data/docs")


def _fetch_target() -> dict[str, Any]:
    rows = execute_supabase(
        "dry-run: fetch Dhoot document",
        supabase.table("documents").select("*").eq("id", DOCUMENT_ID),
    ).data or []
    if len(rows) != 1 or rows[0].get("document_name") != DOCUMENT_NAME:
        raise RuntimeError("Target guard failed: expected exactly the Dhoot document row.")
    return rows[0]


def _fetch_rows(table: str, columns: str = "*") -> list[dict[str, Any]]:
    return execute_supabase(
        f"dry-run: fetch Dhoot {table}",
        supabase.table(table).select(columns).eq("document_id", DOCUMENT_ID).limit(1000),
    ).data or []


def _target_r2_keys(document: dict[str, Any], assets: list[dict[str, Any]]) -> list[str]:
    original_key = document.get("storage_key") or document_object_key(DOCUMENT_ID, DOCUMENT_NAME)
    expected_original = document_object_key(DOCUMENT_ID, DOCUMENT_NAME)
    if original_key != expected_original:
        raise RuntimeError(f"Target guard failed: unexpected original R2 key: {original_key}")

    asset_prefix = f"documents/{DOCUMENT_ID}/assets/"
    asset_keys = [row.get("storage_key") for row in assets if row.get("storage_key")]
    if any(not key.startswith(asset_prefix) for key in asset_keys):
        raise RuntimeError("Target guard failed: an asset R2 key is outside the Dhoot prefix.")
    return [original_key, *sorted(set(asset_keys))]


def _plan() -> dict[str, Any]:
    document = _fetch_target()
    chunks = _fetch_rows("document_chunks", "id,document_id")
    assets = _fetch_rows("document_assets", "id,document_id,asset_type,storage_key")
    keys = _target_r2_keys(document, assets)
    object_status = {key: object_exists(key) for key in keys}
    local_pdf = DOCS_PATH / DOCUMENT_NAME
    local_hash = compute_document_hash(local_pdf) if local_pdf.exists() else None
    local_pdf_names = sorted(path.name for path in DOCS_PATH.glob("*.pdf")) if DOCS_PATH.exists() else []

    return {
        "document": DOCUMENT_NAME,
        "document_id": DOCUMENT_ID,
        "document_rows": 1,
        "document_chunk_rows": len(chunks),
        "document_asset_rows": len(assets),
        "table_assets": sum(row.get("asset_type") == "table" for row in assets),
        "chart_assets": sum(row.get("asset_type") == "chart" for row in assets),
        "r2_original_objects": 1,
        "r2_asset_objects": len(keys) - 1,
        "r2_objects_existing": sum(object_status.values()),
        "local_source_exists": local_pdf.exists(),
        "local_pdf_names": local_pdf_names,
        "local_source_sha256": local_hash,
        "document_hash": document.get("document_hash"),
        "target_scope": {
            "supabase_filter": f"document_id = {DOCUMENT_ID}",
            "r2_original_prefix": f"documents/{DOCUMENT_ID}/original/",
            "r2_asset_prefix": f"documents/{DOCUMENT_ID}/assets/",
        },
        "_document": document,
        "_assets": assets,
        "_keys": keys,
    }


def dry_run() -> dict[str, Any]:
    plan = _plan()
    print("DHOOT_REINGEST_DRY_RUN", {key: value for key, value in plan.items() if not key.startswith("_")})
    print("DHOOT_R2_TARGETS", {"original": 1, "asset_json": plan["r2_asset_objects"], "existing": plan["r2_objects_existing"]})
    print("DHOOT_SCOPE_GUARD", "PASS")
    return plan


def apply(plan: dict[str, Any]) -> None:
    """Apply the guarded sequence only when explicitly requested by an operator."""
    if plan["document_id"] != DOCUMENT_ID or plan["document"] != DOCUMENT_NAME:
        raise RuntimeError("Apply guard failed: target identity changed.")
    if not plan["local_source_exists"] or plan["local_source_sha256"] != plan["document_hash"]:
        raise RuntimeError("Apply guard failed: local PDF does not match the document hash.")
    if plan["local_pdf_names"] != [DOCUMENT_NAME]:
        raise RuntimeError("Apply guard failed: data/docs must contain only the Dhoot PDF.")

    execute_supabase("delete Dhoot document assets", supabase.table("document_assets").delete().eq("document_id", DOCUMENT_ID))
    execute_supabase("delete Dhoot document chunks", supabase.table("document_chunks").delete().eq("document_id", DOCUMENT_ID))
    execute_supabase("delete Dhoot document", supabase.table("documents").delete().eq("id", DOCUMENT_ID))
    for key in plan["_keys"]:
        if object_exists(key):
            delete_object(key)

    inserted = load_chunk_documents()
    print("DHOOT_REINGEST_APPLIED", {"document_id": DOCUMENT_ID, "chunks_reported": inserted})


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely plan or apply Dhoot-only re-ingestion.")
    parser.add_argument("--apply", action="store_true", help="Apply the guarded cleanup and one ingestion.")
    args = parser.parse_args()
    plan = dry_run()
    if args.apply:
        apply(plan)


if __name__ == "__main__":
    main()
