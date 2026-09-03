from langchain_openai import ChatOpenAI
from core.settings import settings


def generate_investment_verdict(decision_report: list[dict]) -> dict:
    confidences = [q["confidence_score"] for q in decision_report]
    avg_confidence = sum(confidences) / len(confidences)

    insufficient_count = sum(
        1 for q in decision_report
        if "insufficient disclosure" in q["answer"].lower()
    )
    if avg_confidence >= 70 and insufficient_count <= 2:
        verdict = "PROCEED"
    elif avg_confidence >= 45:
        verdict = "CAUTION"
    else:
        verdict = "AVOID"

    summary_prompt = f"""
You are an investment analyst.

Based ONLY on the following DRHP-based analysis outcomes,
write a concise 120–150 word investment verdict justification.

Rules:
- Use professional, neutral tone
- No speculation
- No markdown
- Reference disclosure quality and risk concentration

Inputs:
Average confidence score: {round(avg_confidence, 1)}
Number of insufficient disclosure flags: {insufficient_count}
Final verdict: {verdict}

Decision data:
{decision_report}

Return ONLY plain text.
"""

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
