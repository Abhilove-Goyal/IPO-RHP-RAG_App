"""
Prompt construction for RAG using analyst-grade information formatting.

Provides formatted context with full metadata for transparency and traceability.
"""

import json

from rag.intent_classification import classify_intent, STYLE_RULES


def format_evidence(context_chunks):
    """
    Format evidence from chunks with full metadata.
    
    Each evidence block includes explicit provenance labels and preserves
    structured table or asset metadata when it is available.
    
    Args:
        context_chunks: List of chunk dictionaries
    
    Returns:
        Formatted evidence string
    """
    evidence = []

    for i, c in enumerate(context_chunks, 1):
        metadata = c.get("metadata") or {}
        page = c.get("page_number", metadata.get("page_number", "?"))
        section = c.get("section", metadata.get("section", "Unknown Section"))
        subsection = c.get("subsection", metadata.get("subsection")) or "Not specified"
        doc = c.get("document_name", metadata.get("document_name", "Unknown Document"))
        source_type = c.get("source_type", metadata.get("source_type", "text"))
        text = c.get("chunk_text", "").strip()

        lines = [
            f"[{i}] [Document: {doc}]",
            f"[Page: {page}]",
            f"[Section: {section}]",
            f"[Subsection: {subsection}]",
            f"[Source: {source_type}]",
        ]

        asset_reference = (
            c.get("source_identifier")
            or metadata.get("source_identifier")
            or c.get("asset_id")
            or metadata.get("asset_id")
        )
        asset_type = c.get("asset_type") or metadata.get("asset_type")
        caption = c.get("caption") or metadata.get("caption")
        if asset_reference or asset_type or caption:
            lines.append(
                "[Asset: "
                + json.dumps(
                    {
                        "reference": asset_reference,
                        "type": asset_type,
                        "caption": caption,
                    },
                    ensure_ascii=True,
                )
                + "]"
            )

        if source_type == "table" or asset_type == "table":
            headers = metadata.get("headers")
            rows = metadata.get("rows")
            if headers:
                lines.append(f"[Table headers: {json.dumps(headers, ensure_ascii=True)}]")
            if rows:
                lines.append(f"[Table rows: {json.dumps(rows, ensure_ascii=True)}]")

        lines.append(f"[Chunk text]\n{text}")
        evidence.append("\n".join(lines))

    return "\n\n".join(evidence)


def build_prompt(question: str, context_chunks: list[dict]) -> str:
    """
    Build professional analyst prompt with full context and metadata.
    
    Args:
        question: User's question
        context_chunks: Retrieved and reranked chunks
    
    Returns:
        Complete prompt for LLM
    """
    intent = classify_intent(question)
    style_rules = STYLE_RULES[intent.value]
    evidence_context = format_evidence(context_chunks)

    # Enhanced prompt with metadata awareness
    prompt = f"""You are a financial and IPO decision-analysis assistant.

You must answer strictly and exclusively from the supplied evidence extracted from a Draft Red Herring Prospectus (DRHP) or IPO filing document.

CRITICAL GUIDELINES:
1. Base every factual statement and number on the supplied evidence.
2. Never invent, estimate, round, or silently correct financial figures.
3. Preserve the exact numerical precision, units, currencies, years, and percentages shown.
4. Distinguish source facts from calculations or inferences. Show a calculation only when all inputs are supplied, label it as a calculation, and do not present it as a disclosed fact.
5. If the evidence is insufficient, explicitly say that the supplied document evidence does not disclose enough information to answer.
6. Do not claim that the evidence is complete or representative of the whole filing.
7. Cite supporting evidence using human-readable citations in the form [Page N, Section] and add " — Table" or " — Asset" when applicable.
8. Do not fabricate page numbers, sections, table values, chart values, or citations.
9. Treat [Source: table] evidence as structured financial data. Keep row/column meaning, headers, units, and year alignment intact.
10. Use chart or image information only when its caption or extracted metadata is explicitly supplied; otherwise state that the numerical chart detail is unavailable.
11. Flag contradictions or ambiguity between supplied evidence blocks.
12. Do not expose internal database IDs unless they are necessary to disambiguate evidence.

FORMATTING RULES:
- Use professional financial language
- Do NOT use markdown, bullet points, or heading symbols
- Write in clear, professional analyst tone
- Structure answer logically with clear transitions

ANSWER STYLE GUIDELINES:
{style_rules}

REFERENCE MATERIALS:
{evidence_context}

QUESTION TO ANSWER:
{question}

RESPONSE FORMAT:
Return only the final professional answer as plain text. Include inline citations such as [Page 4, Business] or [Page 6, Financial Information — Table]. Do not return JSON, markdown headings, or a citation that is not directly supported by the evidence.

Now provide your answer:"""
    
    return prompt

