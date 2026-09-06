from langchain_openai import ChatOpenAI
from core.settings import settings


def generate_investment_verdict(decision_report: list[dict]) -> dict:
    if not decision_report:
        return {
            "verdict": "AVOID",
            "average_confidence": 0.0,
            "insufficient_disclosure_flags": 0,
            "reasoning": "No decision report data available to generate an investment verdict.",
        }

    confidences = [q.get("confidence_score", 0.0) for q in decision_report]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    insufficient_count = sum(
        1 for q in decision_report
        if "insufficient disclosure" in (q.get("answer") or "").lower()
        or "does not disclose sufficient" in (q.get("answer") or "").lower()
    )
    if avg_confidence >= 70 and insufficient_count <= 2:
        verdict = "PROCEED"
    elif avg_confidence >= 45:
        verdict = "CAUTION"
    else:
        verdict = "AVOID"

    summary_prompt = f"""You are an investment analyst synthesizing an IPO decision report.

Based ONLY on the following 8 DRHP-based analysis question answers, write a concise 120–150 word investment verdict justification.

Rules:
- Base your justification strictly on the 8 answers provided below.
- Do NOT assume or introduce external or unsupported facts.
- Use a professional, neutral financial tone.
- Do NOT use markdown.
- Reference disclosure completeness, key identified risks, and financial viability where disclosed.
- If certain critical areas were undisclosed or insufficient in the answers, reflect that limitation directly.

Inputs:
Average confidence score: {round(avg_confidence, 1)}
Number of insufficient disclosure flags: {insufficient_count}
Final verdict: {verdict}

Decision Report Answers:
{decision_report}

Return ONLY plain text."""

    try:
        llm = ChatOpenAI(
            model=settings.groq_model,
            api_key=settings.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
            temperature=0,
        )
        reasoning = llm.invoke(summary_prompt).content.strip()
    except Exception as e:
        print(f"API Error in investment verdict generation: {str(e)}")
        reasoning = f"Unable to generate detailed reasoning due to API service issues. Based on the confidence scores, the recommendation is {verdict}. Error: {str(e)[:100]}..."

    return {
        "verdict": verdict,
        "average_confidence": round(avg_confidence, 1),
        "insufficient_disclosure_flags": insufficient_count,
        "reasoning": reasoning
    }
