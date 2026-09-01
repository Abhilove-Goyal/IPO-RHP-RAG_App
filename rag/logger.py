"""
Query result logging for audit and analytics.
"""

import uuid
from core.supabase_client import supabase


def log_result(data: dict):
    """
    Log query results for audit and analytics.
    
    Args:
        data: Dictionary with keys:
            - query: User question
            - answer: Generated answer
            - ipo_id: Document ID
            - chunks_used: Number of chunks (optional)
            - faithfulness: Confidence score (optional)
    """
    try:
        trace_id = uuid.uuid4()

        payload = {
            "trace_id": str(trace_id),
            "document_id": data.get("document_id") or data.get("ipo_id", "unknown"),
            "query": data.get("query", ""),
            "answer": data.get("answer", "")[:500],  # Truncate long answers
            "chunks_used": data.get("chunks_used", 0),
            "faithfulness": data.get("faithfulness", 0.0),
            "retrieved_chunks": data.get("context", [])
        }
        
        # Insert into Supabase if table exists
        try:
            supabase.table("rag_traces").insert(payload).execute()
        except Exception as e:
            print(f"[LOGGER] Warning: Could not log to Supabase: {e}")
            
    except Exception as e:
        print(f"[LOGGER] Error logging result: {e}")

