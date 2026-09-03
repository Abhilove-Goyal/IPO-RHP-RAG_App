# Dhoot Re-ingestion Runbook

The utility in `scripts/dhoot_reingest.py` is read-only by default and is hard-coded to:

- Document: `Dhoot Transmission Limited - AP_p.pdf`
- Document ID: `cc59e2bb-a891-5162-b64a-0fa7ebb30362`

## Dry Run

```powershell
.\venv\Scripts\python.exe scripts\dhoot_reingest.py
```

This reads the target `documents`, `document_chunks`, and `document_assets` rows and checks only the target document's deterministic R2 original and asset prefixes. It prints counts and scope guards. It performs no writes.

## Controlled Apply

Run only after reviewing the dry-run output:

```powershell
.\venv\Scripts\python.exe scripts\dhoot_reingest.py --apply
```

The apply sequence is:

1. Revalidate the exact document ID, filename, and local PDF hash.
2. Delete only this document's `document_assets` rows.
3. Delete only this document's `document_chunks` rows.
4. Delete only this document's `documents` row.
5. Delete only the snapshotted Dhoot original and asset JSON R2 objects.
6. Run `load_chunk_documents()` once against the local Dhoot PDF.
7. The ingestion flow extracts assets, generates table-aware chunks, regenerates 1024-dimensional embeddings, and persists the chunks/assets. BM25 loads current `document_chunks` rows per search, so no separate rebuild is required.

The utility does not modify schema, R2 layout, retrieval parameters, embedding configuration, or unrelated documents. The apply mode is not part of local tests and must not be run automatically.
