import json
from langchain_openai import ChatOpenAI

from core.settings import settings
import core.runtime_state as runtime

from rag.retriever import hybrid_search, rerank_hybrid_candidates
from rag.prompt_builder import estimate_prompt_tokens, format_evidence, select_context_chunks
from rag.ingestion import load_chunk_documents
from rag.non_negotiable_questions import NON_NEGOTIABLE_QUESTIONS
from core.supabase_client import get_document_stats


def sanitize(obj):
    if obj is ...:
        return None
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(i) for i in obj]
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


def generate_decision_report(document_id: str | None = None):
    ipo_id = document_id or runtime.get_current_ipo()
    if not ipo_id:
        raise ValueError("No IPO uploaded or selected.")

    ensure_embeddings_exist(ipo_id)
    llm = ChatOpenAI(
        model=settings.groq_model,
        api_key=settings.groq_api_key,
        base_url="https://api.groq.com/openai/v1",
        temperature=0,
    )

    question_contexts = []
    all_context = []
    for question in NON_NEGOTIABLE_QUESTIONS:
        fused_chunks = hybrid_search(question["display_question"], ipo_id)
        reranked_chunks = rerank_hybrid_candidates(
            question["display_question"], fused_chunks, top_k=settings.final_top_k
        )
        selected = select_context_chunks(question["display_question"], reranked_chunks, token_budget=900)
        question_contexts.append((question, selected))
        all_context.extend(selected)

    unique_context = []
    seen = set()
    for chunk in all_context:
        key = chunk.get("id") or chunk.get("chunk_id") or chunk.get("chunk_text")
        if key not in seen:
            seen.add(key)
            unique_context.append(chunk)
    shared_context = select_context_chunks("IPO offer financial risks", unique_context, token_budget=1600)
    evidence_context = format_evidence(shared_context)
    question_instructions = "\n\n".join(
        f"{index}. ID: {question['id']}\nQuestion: {question['display_question']}"
        for index, (question, _) in enumerate(question_contexts, 1)
    )
    prompt = f"""
You are a financial analyst preparing an investment decision report
based strictly on a Draft Red Herring Prospectus (DRHP).

Answer all questions below using ONLY the shared evidence.

{question_instructions}

You must ONLY use the evidence provided.

Rules:
- Do NOT invent facts.
- If evidence is insufficient for one question, say so for that question only.
- Every factual statement must be traceable to the evidence.
- Return confidence_score as a percentage from 0 to 100, not a decimal from 0 to 1.

Shared evidence:
{evidence_context}

Return ONLY a valid JSON array in this exact format:
[
  {{
    "id": "question_id",
    "answer": "...",
    "pros": ["..."],
    "cons": ["..."],
    "confidence_score": 0,
    "citations": [{{"page": 123}}]
  }}
]
"""
    print(f"[REPORT] Prompt estimate: {estimate_prompt_tokens(prompt)} tokens")

    try:
        response = llm.invoke(prompt).content.strip()
        parsed_results = json.loads(response)
        if not isinstance(parsed_results, list):
            raise ValueError("Report response was not a JSON array")
        by_id = {item.get("id"): item for item in parsed_results if isinstance(item, dict)}
    except Exception as exc:
        print(f"[REPORT ERROR] {exc}")
        by_id = {}

    report = []
    for question, _ in question_contexts:
        parsed = by_id.get(question["id"], {
            "answer": "Analysis failed due to API error.",
            "pros": [],
            "cons": ["API service issue"],
            "confidence_score": 0,
            "citations": [],
        })
        parsed["confidence_score"] = normalize_confidence_score(parsed.get("confidence_score"))
        report.append(sanitize({
            "id": question["id"],
            "question": question["display_question"],
            **parsed,
        }))
    return report
