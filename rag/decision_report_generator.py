import json

from core.settings import settings
import core.runtime_state as runtime

from rag.retriever import hybrid_search, rerank_hybrid_candidates
from rag.prompt_builder import estimate_prompt_tokens, format_evidence, select_context_chunks
from rag.ingestion import load_chunk_documents
from rag.non_negotiable_questions import NON_NEGOTIABLE_QUESTIONS
from rag.model_routing import invoke_with_fallback
from core.supabase_client import get_document_stats


def sanitize(obj):
    if obj is ...:
        return None
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(i) for i in obj]
    if isinstance(obj, str):
        cleaned = "".join(character for character in obj if character in "\n\r\t" or ord(character) >= 32)
        return cleaned if cleaned.strip() else obj
    return obj


def normalize_confidence_score(value) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if 0.0 <= score <= 1.0:
        score *= 100.0
    return round(max(0.0, min(100.0, score)), 1)


def ensure_embeddings_exist(ipo_id: str):
    result = get_document_stats(ipo_id)
    if result.count == 0:
        print(f"[REPORT] No embeddings found for {ipo_id}. Running ingestion.")
        load_chunk_documents()


def _question_prompt(question: dict, evidence_context: str) -> str:
    return f"""
You are a financial analyst preparing an investment decision report based strictly on a DRHP.

Question: {question['display_question']}
Instructions: {question['analysis_prompt']}

Use ONLY the evidence below. Do not invent facts. Preserve exact financial numbers and citations.
Evidence:
{evidence_context}

Return ONLY valid JSON in this exact format:
{{"answer":"...","pros":[],"cons":[],"confidence_score":0,"citations":[{{"page":123}}]}}
"""


def _failed_result(status: str) -> dict:
    return {
        "answer": "",
        "pros": [],
        "cons": ["Model/API failure"],
        "confidence_score": 0,
        "citations": [],
        "status": status,
    }


def generate_decision_report(document_id: str | None = None):
    ipo_id = document_id or runtime.get_current_ipo()
    if not ipo_id:
        raise ValueError("No IPO uploaded or selected.")

    ensure_embeddings_exist(ipo_id)
    report = []
    for question in NON_NEGOTIABLE_QUESTIONS:
        fused_chunks = hybrid_search(question["display_question"], ipo_id)
        reranked_chunks = rerank_hybrid_candidates(
            question["display_question"], fused_chunks, top_k=settings.final_top_k
        )
        selected = select_context_chunks(question["display_question"], reranked_chunks, token_budget=900)
        result = invoke_with_fallback(_question_prompt(question, format_evidence(selected)))
        if result.status != "SUCCESS":
            parsed = _failed_result(result.status)
        else:
            try:
                parsed = json.loads(result.text)
                if not isinstance(parsed, dict):
                    raise ValueError("Question response was not a JSON object")
                parsed["status"] = "SUCCESS"
            except (json.JSONDecodeError, TypeError, ValueError):
                parsed = _failed_result("MODEL_ERROR")

        parsed["confidence_score"] = normalize_confidence_score(parsed.get("confidence_score"))
        report.append(sanitize({
            "id": question["id"],
            "question": question["display_question"],
            **parsed,
        }))
    return report
