import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api
import main
from fastapi import HTTPException


DOCUMENT_ID = "cc59e2bb-a891-5162-b64a-0fa7ebb30362"
OTHER_DOCUMENT_ID = "11111111-1111-4111-8111-111111111111"


class DocumentSelectionTests(unittest.TestCase):
    def test_completed_documents_are_exposed_without_internal_fields(self):
        result = SimpleNamespace(data=[
            {"id": DOCUMENT_ID, "document_name": "Dhoot.pdf", "processing_status": "completed", "document_hash": "secret"},
            {"id": OTHER_DOCUMENT_ID, "document_name": "Pending.pdf", "processing_status": "processing"},
        ])
        with patch.object(api, "execute_supabase", return_value=result):
            response = api.completed_ipos()

        self.assertEqual(response, {"ipos": [{"id": DOCUMENT_ID, "name": "Dhoot.pdf", "status": "completed"}]})

    def test_explicit_document_id_is_validated_and_passed_to_run(self):
        database_result = SimpleNamespace(data=[{"id": DOCUMENT_ID}])
        with patch.object(api, "get_current_ipo", return_value=None), patch.object(
            api, "execute_supabase", return_value=database_result
        ), patch.object(api, "set_current_ipo") as set_current, patch.object(
            api, "run", return_value=("answer", 1.0)
        ) as run:
            response = api.ask(api.QueryRequest(query="test question", document_id=DOCUMENT_ID))

        self.assertEqual(response["status"], "success")
        set_current.assert_called_once_with(DOCUMENT_ID)
        run.assert_called_once_with("test question", document_id=DOCUMENT_ID)

    def test_ask_returns_successful_answer(self):
        database_result = SimpleNamespace(data=[{"id": DOCUMENT_ID}])
        with patch.object(api, "execute_supabase", return_value=database_result), patch.object(
            api, "set_current_ipo"
        ), patch.object(api, "run", return_value=("**OFS**: 19,137,602 shares", 1.0)):
            response = api.ask(api.QueryRequest(query="What is the OFS size?", document_id=DOCUMENT_ID))

        self.assertEqual(response["answer"], "**OFS**: 19,137,602 shares")

    def test_no_selected_document_returns_clear_error(self):
        with patch.object(api, "get_current_ipo", return_value=None):
            with self.assertRaises(HTTPException) as raised:
                api.ask(api.QueryRequest(query="test question"))

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("No IPO selected", raised.exception.detail)

    def test_invalid_document_id_is_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            api.ask(api.QueryRequest(query="test question", document_id="dhoot-transmission"))

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Invalid document_id", raised.exception.detail)

    def test_nonexistent_or_incomplete_document_is_rejected(self):
        with patch.object(api, "execute_supabase", return_value=SimpleNamespace(data=[])):
            with self.assertRaises(HTTPException) as raised:
                api.ask(api.QueryRequest(query="test question", document_id=OTHER_DOCUMENT_ID))

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("not found or is not completed", raised.exception.detail)

    def test_upload_response_uses_canonical_document_uuid(self):
        document = {"id": DOCUMENT_ID, "document_name": "Dhoot.pdf", "company_name": "Dhoot Transmission Limited"}
        import tempfile

        with tempfile.TemporaryDirectory() as directory, patch.object(api.settings, "docs_dir", Path(directory)), patch.object(
            api, "load_chunk_documents", return_value=12
        ), patch.object(api, "get_document_by_hash", return_value=SimpleNamespace(data=[document])), patch.object(
            api, "set_current_ipo"
        ), patch.object(api, "reset_chunks"):
            from fastapi import UploadFile
            from io import BytesIO

            upload = UploadFile(filename="new.pdf", file=BytesIO(b"pdf"))
            response = self._run_async(api.upload_pdf(upload))

        self.assertEqual(response["document_id"], DOCUMENT_ID)
        self.assertEqual(response["name"], "Dhoot Transmission Limited")

    def test_main_run_uses_explicit_document_id(self):
        with patch.object(main, "ensure_embeddings_exist"), patch.object(
            main, "get_sections_for_ipo", return_value=[]
        ), patch.object(main, "hybrid_search", return_value=[{"chunk_text": "evidence"}]) as search, patch.object(
            main, "rerank_hybrid_candidates", return_value=[]
        ), patch.object(main, "generate_answer", return_value=("answer", 1.0)), patch.object(
            main, "log_result"
        ):
            main.run("question", document_id=DOCUMENT_ID)

        self.assertEqual(search.call_args.kwargs["document_id"], DOCUMENT_ID)

    def test_decision_report_uses_explicit_document_id(self):
        with patch.object(api, "resolve_active_document_id", return_value=DOCUMENT_ID), patch.object(
            api, "set_current_ipo"
        ), patch.object(api, "generate_decision_report", return_value=[]) as report, patch.object(
            api, "generate_investment_verdict", return_value={"verdict": "CAUTION"}
        ):
            response = api.decision_report(api.DecisionReportRequest(document_id=DOCUMENT_ID))

        self.assertEqual(response["report"], [])
        report.assert_called_once_with(document_id=DOCUMENT_ID)

    @staticmethod
    def _run_async(awaitable):
        import asyncio
        return asyncio.run(awaitable)


if __name__ == "__main__":
    unittest.main()
