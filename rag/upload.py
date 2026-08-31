import os
from core.settings import settings
from core.supabase_client import list_documents, get_document_stats, delete_document

# Ensure directory exists
os.makedirs(settings.data_path, exist_ok=True)


def list_all_ipos():
    response = list_documents()
    ipos = []
    for row in response.data:
        document_id = row.get("id") or row.get("document_id")
        if not document_id:
            continue
        count_response = get_document_stats(document_id)
        chunks = count_response.count or 0
        ipos.append({"ipo_id": document_id, "chunks": chunks})
    return ipos


def delete_ipo_vectors(ipo_id: str):
    delete_document(ipo_id)
    print(f"IPO {ipo_id} vectors and metadata deleted")


def get_ipo_stats(ipo_id: str):
    response = get_document_stats(ipo_id)
    return {"total_chunks": response.count or 0}
