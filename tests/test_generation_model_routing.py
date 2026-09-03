import sys
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag.decision_report_generator import generate_decision_report, sanitize
from rag.generator import clean_answer
from rag.model_routing import ModelCallResult, invoke_with_fallback
from rag.non_negotiable_questions import NON_NEGOTIABLE_QUESTIONS


def test_cleanup_preserves_financial_answer_and_citations():
    answer = "Fresh issue: ₹14,000 million (18.31%) [Page 4, Financial Information]"
    assert clean_answer(answer) == answer
    assert sanitize(answer) == answer


def test_cleanup_removes_only_control_characters():
    assert clean_answer("Revenue\x00: ₹1,234.50\n- 18%") == "Revenue: ₹1,234.50\n- 18%"


def test_413_triggers_fallback():
    primary = Mock()
    primary.invoke.side_effect = RuntimeError("413 Request too large; TPM limit")
    fallback = Mock()
    fallback.invoke.return_value.content = "fallback answer"
    with patch("rag.model_routing.ChatOpenAI", side_effect=[primary, fallback]), patch(
        "rag.model_routing.settings.groq_model", "primary"
    ), patch("rag.model_routing.settings.llm_model", "fallback"):
        result = invoke_with_fallback("prompt")
    assert result.status == "SUCCESS"
    assert result.text == "fallback answer"


def test_timeout_triggers_fallback():
    primary = Mock()
    primary.invoke.side_effect = TimeoutError("request timeout")
    fallback = Mock()
    fallback.invoke.return_value.content = "fallback answer"
    with patch("rag.model_routing.ChatOpenAI", side_effect=[primary, fallback]), patch(
        "rag.model_routing.settings.groq_model", "primary"
    ), patch("rag.model_routing.settings.llm_model", "fallback"):
        result = invoke_with_fallback("prompt")
    assert result.status == "SUCCESS"


def test_successful_primary_does_not_invoke_fallback():
    primary = Mock()
    primary.invoke.return_value.content = "primary answer"
    with patch("rag.model_routing.ChatOpenAI", return_value=primary) as factory, patch(
        "rag.model_routing.settings.groq_model", "primary"
    ), patch("rag.model_routing.settings.llm_model", "fallback"):
        result = invoke_with_fallback("prompt")
    assert result.text == "primary answer"
    assert factory.call_count == 1


def test_report_question_failure_is_independent():
    evidence = [{"chunk_text": "evidence", "page_number": 1, "source_type": "text"}]
    responses = [
        ModelCallResult('{"answer":"ok","pros":[],"cons":[],"confidence_score":80}', "SUCCESS", "primary")
        if index != 2 else ModelCallResult(None, "RATE_LIMIT", "fallback")
        for index in range(len(NON_NEGOTIABLE_QUESTIONS))
    ]
    with patch("rag.decision_report_generator.ensure_embeddings_exist"), patch(
        "rag.decision_report_generator.hybrid_search", return_value=evidence
    ), patch("rag.decision_report_generator.rerank_hybrid_candidates", return_value=evidence), patch(
        "rag.decision_report_generator.invoke_with_fallback", side_effect=responses
    ):
        report = generate_decision_report("document")
    assert len(report) == len(NON_NEGOTIABLE_QUESTIONS)
    assert report[2]["status"] == "RATE_LIMIT"
    assert report[0]["status"] == "SUCCESS"
    assert report[3]["status"] == "SUCCESS"


def test_failure_is_not_converted_to_not_disclosed():
    failed = ModelCallResult(None, "MODEL_ERROR", "fallback")
    assert failed.status == "MODEL_ERROR"
    assert "not disclosed" not in (sanitize({"answer": "", "status": failed.status})["answer"])


def test_true_not_disclosed_survives_cleanup():
    assert clean_answer("Information is not disclosed.") == "Information is not disclosed."