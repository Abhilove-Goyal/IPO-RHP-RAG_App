import sys
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag.decision_report_generator import generate_decision_report, normalize_confidence_score, sanitize
from rag.model_routing import ModelCallResult
from rag.non_negotiable_questions import NON_NEGOTIABLE_QUESTIONS
from rag.investment_verdict import generate_investment_verdict
from rag.investor_snapshot import InvestorSnapshot, build_investor_snapshot


DOCUMENT_ID = "11111111-1111-4111-8111-111111111111"
EXPECTED_QUESTION_IDS = [
    "business_industry",
    "growth_competitive_position",
    "ipo_capital_structure",
    "shareholding_capital_history",
    "objectives_of_offer",
    "financial_performance_ratios",
    "material_risks",
    "litigation_contingencies",
]


def test_report_has_exactly_eight_core_questions():
    assert len(NON_NEGOTIABLE_QUESTIONS) == 8
    actual_ids = [q["id"] for q in NON_NEGOTIABLE_QUESTIONS]
    assert actual_ids == EXPECTED_QUESTION_IDS


def test_each_question_executes_independently():
    evidence = [{
        "chunk_text": "Evidence for question",
        "document_id": DOCUMENT_ID,
        "page_number": 1,
        "section": "general",
        "source_type": "text",
        "metadata": {"source_type": "text"},
    }]

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

    assert len(report) == 8
    assert hybrid.call_count == 8
    assert rerank.call_count == 8
    assert llm_class.call_count == 8
    assert all(item["answer"] == "Supported answer" for item in report)


def test_selected_document_id_is_respected():
    with patch("rag.decision_report_generator.ensure_embeddings_exist"), patch(
        "rag.decision_report_generator.hybrid_search", return_value=[]
    ) as hybrid, patch(
        "rag.decision_report_generator.rerank_hybrid_candidates", return_value=[]
    ), patch(
        "rag.decision_report_generator.invoke_with_fallback",
        return_value=ModelCallResult('{"answer":"ok"}', "SUCCESS", "model"),
    ):
        generate_decision_report(document_id=DOCUMENT_ID)

    assert hybrid.call_count == 8
    assert all(call.args[1] == DOCUMENT_ID for call in hybrid.call_args_list)


def test_no_giant_combined_llm_request():
    prompts_seen = []

    def mock_invoke(prompt):
        prompts_seen.append(prompt)
        return ModelCallResult('{"answer":"ok","confidence_score":75}', "SUCCESS", "model")

    with patch("rag.decision_report_generator.ensure_embeddings_exist"), patch(
        "rag.decision_report_generator.hybrid_search", return_value=[]
    ), patch(
        "rag.decision_report_generator.rerank_hybrid_candidates", return_value=[]
    ), patch(
        "rag.decision_report_generator.invoke_with_fallback", side_effect=mock_invoke
    ):
        report = generate_decision_report(document_id=DOCUMENT_ID)

    assert len(prompts_seen) == 8
    for i, question in enumerate(NON_NEGOTIABLE_QUESTIONS):
        assert question["display_question"] in prompts_seen[i]


def test_missing_evidence_produces_insufficiency_answer():
    insufficient_response = (
        '{"answer":"The supplied document evidence does not disclose sufficient information.",'
        '"pros":[],"cons":["Insufficient disclosure in filing"],"confidence_score":20,"citations":[]}'
    )
    with patch("rag.decision_report_generator.ensure_embeddings_exist"), patch(
        "rag.decision_report_generator.hybrid_search", return_value=[]
    ), patch(
        "rag.decision_report_generator.rerank_hybrid_candidates", return_value=[]
    ), patch(
        "rag.decision_report_generator.invoke_with_fallback",
        return_value=ModelCallResult(insufficient_response, "SUCCESS", "model"),
    ):
        report = generate_decision_report(document_id=DOCUMENT_ID)

    assert len(report) == 8
    assert "does not disclose sufficient information" in report[0]["answer"]
    assert report[0]["status"] == "SUCCESS"


def test_api_failures_do_not_look_like_disclosure_quality():
    report = [{"confidence_score": 0, "answer": "", "status": "MODEL_ERROR", "cons": ["Model/API failure"]}]
    with patch("rag.investment_verdict.ChatOpenAI"):
        verdict = generate_investment_verdict(report)

    assert verdict["verdict"] == "AVOID"
    assert "not disclosed" not in report[0]["answer"].lower()


def test_fractional_confidence_is_normalized_to_percentage():
    assert normalize_confidence_score(0.35) == 35.0
    assert normalize_confidence_score(0.92) == 92.0
    assert normalize_confidence_score(87) == 87.0
    assert normalize_confidence_score("invalid") == 0.0


def test_infographic_snapshot_does_not_invent_missing_fields():
    # 1. Empty report -> all fields None
    empty_snapshot = build_investor_snapshot([])
    assert all(value is None for value in empty_snapshot.values())

    # 2. Report with only partial disclosure -> only disclosed fields populated
    partial_report = [
        {
            "id": "business_industry",
            "status": "SUCCESS",
            "answer": "Company operates in automotive components.",
            "key_metrics": {"company": "Dhoot Transmission Limited", "industry": "Automotive"},
        },
        {
            "id": "ipo_capital_structure",
            "status": "SUCCESS",
            "answer": "Fresh issue of ₹14,000 million. OFS is not disclosed.",
            "key_metrics": {"fresh_issue": "₹14,000 million", "ofs": "Not disclosed"},
        },
        {
            "id": "financial_performance_ratios",
            "status": "MODEL_ERROR",  # Failed question
            "answer": "",
            "key_metrics": {"revenue": "₹50,000 million"},  # Should NOT be extracted due to failure
        },
    ]

    snapshot = build_investor_snapshot(partial_report)
    assert snapshot["company"] == "Dhoot Transmission Limited"
    assert snapshot["industry"] == "Automotive"
    assert snapshot["fresh_issue"] == "₹14,000 million"
    assert snapshot["ofs"] is None  # "Not disclosed" must not be treated as a value
    assert snapshot["total_offer"] is None  # Missing field stays None
    assert snapshot["revenue"] is None  # Failed question metrics must NOT be extracted
    assert snapshot["pat"] is None
    assert snapshot["roe"] is None
    assert snapshot["debt"] is None
    assert snapshot["major_growth_driver"] is None


def test_final_verdict_generated_from_eight_answers():
    mock_report = [
        {
            "id": q["id"],
            "question": q["display_question"],
            "answer": f"Disclosed details for {q['id']}",
            "confidence_score": 85.0,
            "status": "SUCCESS",
        }
        for q in NON_NEGOTIABLE_QUESTIONS
    ]

    mock_llm = Mock()
    mock_llm.invoke.return_value.content = "Sound investment opportunity based on strong disclosed financials."

    with patch("rag.investment_verdict.ChatOpenAI", return_value=mock_llm):
        verdict = generate_investment_verdict(mock_report)

    assert verdict["verdict"] == "PROCEED"
    assert verdict["average_confidence"] == 85.0
    assert verdict["insufficient_disclosure_flags"] == 0
    assert mock_llm.invoke.call_count == 1
    # Verify prompt includes all 8 question answers
    prompt_sent = mock_llm.invoke.call_args[0][0]
    for q in NON_NEGOTIABLE_QUESTIONS:
        assert q["id"] in prompt_sent



