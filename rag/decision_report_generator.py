import json
from langchain_openai import ChatOpenAI

from core.settings import settings
import core.runtime_state as runtime

from rag.retriever import retrieve_multi
from rag.ingestion import load_chunk_documents
from rag.non_negotiable_questions import NON_NEGOTIABLE_QUESTIONS

from core.supabase_client import get_document_stats


# --------------------------------------------------
# Utility
# --------------------------------------------------

def sanitize(obj):

    if obj is ...:
        return None

    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}

    if isinstance(obj, list):
        return [sanitize(i) for i in obj]

    return obj


# --------------------------------------------------
# Ensure embeddings exist
# --------------------------------------------------

def ensure_embeddings_exist(ipo_id: str):

    result = get_document_stats(ipo_id)

    if result.count == 0:
        print(f"[REPORT] No embeddings found for {ipo_id}. Running ingestion.")
        load_chunk_documents()


# --------------------------------------------------
# Evidence formatter
# --------------------------------------------------

def format_evidence(chunks):

    evidence = []

    for c in chunks:

        if isinstance(c, str):
            page = "unknown"
            text = c
        elif isinstance(c, dict):
            page = c.get("page_number", "unknown")
            text = c.get("chunk_text", "")
        else:
            # Handle unexpected types
            page = "unknown"
            text = str(c)

        # Ensure text is a string and clean it
        if isinstance(text, str):
            text = text.strip().replace("\n", " ")
        else:
            text = str(text)

        evidence.append(f"(page {page}) {text}")

    return "\n\n".join(evidence)


# --------------------------------------------------
# Decision report generator
# --------------------------------------------------

def generate_decision_report():

    ipo_id = runtime.get_current_ipo()

    if not ipo_id:
        raise ValueError("No IPO uploaded or selected.")

    ensure_embeddings_exist(ipo_id)

    llm = ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.groq_api_key,
        base_url="https://api.groq.com/openai/v1",
        temperature=0
    )

    report = []

    for q in NON_NEGOTIABLE_QUESTIONS:

        q_id = q["id"]
        display_question = q["display_question"]
        analysis_prompt = q["analysis_prompt"]

        # --------------------------------
        # Section-aware retrieval
        # --------------------------------

        context_chunks = retrieve_multi(
            query=display_question,
            ipo_id=ipo_id,
            expand_fn=lambda x: [x],
            section_filter=q_id
        )

        # --------------------------------
        # Fallback retrieval
        # --------------------------------

        if not context_chunks:

            context_chunks = retrieve_multi(
                query=display_question,
                ipo_id=ipo_id,
                expand_fn=lambda x: [x]
            )

        if not context_chunks:

            report.append({
                "id": q_id,
                "question": display_question,
                "answer": "The DRHP does not provide sufficient disclosure on this aspect.",
                "pros": [],
                "cons": ["Relevant section not found in the DRHP"],
                "confidence_score": 20,
                "citations": []
            })

            continue

        evidence_context = format_evidence(context_chunks)

        prompt = f"""
You are a financial analyst preparing an investment decision report
based strictly on a Draft Red Herring Prospectus (DRHP).

{analysis_prompt}

You must ONLY use the evidence provided.

Rules:
- Do NOT invent facts.
- If evidence is insufficient, say so.
- Every factual statement must be traceable to the evidence.

Evidence:
{evidence_context}

Question:
{display_question}

Return ONLY valid JSON in this exact format:

{{
  "answer": "...",
  "pros": ["...", "..."],
  "cons": ["...", "..."],
  "confidence_score": 0,
  "citations": [
    {{"page": 123}},
    {{"page": 145}}
  ]
}}
"""

        try:

            response = llm.invoke(prompt).content.strip()

            try:

                parsed = json.loads(response)

            except json.JSONDecodeError:

                parsed = {
                    "answer": "The DRHP does not provide sufficient disclosure on this aspect.",
                    "pros": [],
                    "cons": ["Insufficient disclosure in the DRHP"],
                    "confidence_score": 30,
                    "citations": []
                }

        except Exception as e:

            print(f"[REPORT ERROR] {q_id}: {str(e)}")

            parsed = {
                "answer": "Analysis failed due to API error.",
                "pros": [],
                "cons": ["API service issue"],
                "confidence_score": 0,
                "citations": []
            }

        report.append(sanitize({
            "id": q_id,
            "question": display_question,
            **parsed
        }))

    return report
