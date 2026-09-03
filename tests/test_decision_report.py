import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag.decision_report_generator import generate_decision_report, normalize_confidence_score
from rag.model_routing import ModelCallResult
from rag.non_negotiable_questions import NON_NEGOTIABLE_QUESTIONS
from rag.investment_verdict import generate_investment_verdict
from rag.prompt_builder import estimate_prompt_tokens


DOCUMENT_ID = "11111111-1111-4111-8111-111111111111"


def test_report_uses_hybrid_search_and_jina_for_each_general_question():
    evidence = [{
        "chunk_text": "Revenue and offer evidence",
        "document_id": DOCUMENT_ID,
        "page_number": 1,
        "section": "general",
        "source_type": "text",
        "metadata": {"source_type": "text"},
    }]
    response = "[" + ",".join(
        '{"id":"%s","answer":"Supported answer","pros":[],"cons":[],"confidence_score":80,"citations":[{"page":1}]}' % question["id"]
        for question in NON_NEGOTIABLE_QUESTIONS
    ) + "]"

    with patch("rag.decision_report_generator.ensure_embeddings_exist"), patch(
        "rag.decision_report_generator.hybrid_search", return_value=evidence
    ) as hybrid, patch(
        "rag.decision_report_generator.rerank_hybrid_candidates", return_value=evidence
    ) as rerank, patch(
        "rag.decision_report_generator.invoke_with_fallback",
        return_value=ModelCallResult(
            '{"answer":"Supported answer","pros":[],"cons":[],"confidence_score":80,"citations":[{"page":1}]}',
            "SUCCESS",
            "primary",
        ),
    ) as llm_class:
        report = generate_decision_report(document_id=DOCUMENT_ID)

    assert len(report) == len(NON_NEGOTIABLE_QUESTIONS)
    assert hybrid.call_count == len(NON_NEGOTIABLE_QUESTIONS)
    assert rerank.call_count == len(NON_NEGOTIABLE_QUESTIONS)
    assert all(call.args[1] == DOCUMENT_ID for call in hybrid.call_args_list)
    assert all(item["answer"] == "Supported answer" for item in report)
    assert llm_class.call_count == len(NON_NEGOTIABLE_QUESTIONS)


def test_report_uses_configured_groq_model():
    with patch("rag.decision_report_generator.ensure_embeddings_exist"), patch(
        "rag.decision_report_generator.hybrid_search", return_value=[]
    ), patch("rag.decision_report_generator.rerank_hybrid_candidates", return_value=[]), patch(
        "rag.decision_report_generator.invoke_with_fallback",
        return_value=ModelCallResult('{"answer":"x"}', "SUCCESS", "configured"),
    ):
        generate_decision_report(document_id=DOCUMENT_ID)


def test_fractional_confidence_is_normalized_to_percentage():
    assert normalize_confidence_score(0.35) == 35.0
    assert normalize_confidence_score(0.92) == 92.0
    assert normalize_confidence_score(87) == 87.0
    assert normalize_confidence_score("invalid") == 0.0


def test_api_failures_do_not_look_like_disclosure_quality():
    report = [{"confidence_score": 0, "answer": "Analysis failed due to API error."}]
    with patch("rag.investment_verdict.ChatOpenAI"):
        verdict = generate_investment_verdict(report)

    assert verdict["verdict"] == "AVOID"


