from rag.ingestion import backfill_missing_asset_chunks


if __name__ == "__main__":
    print(f"Inserted asset chunks: {backfill_missing_asset_chunks()}")
