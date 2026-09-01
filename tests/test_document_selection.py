import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api

DOCUMENT_ID = "cc59e2bb-a891-5162-b64a-0fa7ebb30362"


class DocumentSelectionTests(unittest.TestCase):
    def test_fresh_runtime_resolves_existing_completed_document(self):
        database_result = SimpleNamespace(data=[{"id": DOCUMENT_ID}])
        with patch.object(api, "get_current_ipo", return_value=None), patch.object(
            api, "execute_supabase", return_value=database_result
        ), patch.object(api, "set_current_ipo") as set_current, patch.object(
            api, "run", return_value=("answer", 1.0)
        ) as run:
            response = api.ask(api.QueryRequest(query="test question"))

        self.assertEqual(response["status"], "success")
        set_current.assert_called_once_with(DOCUMENT_ID)
        run.assert_called_once_with("test question")

    def test_existing_canonical_runtime_id_is_preserved(self):
        with patch.object(api, "get_current_ipo", return_value=DOCUMENT_ID), patch.object(
            api, "execute_supabase"
        ) as execute, patch.object(api, "set_current_ipo") as set_current, patch.object(
            api, "run", return_value=("answer", 1.0)
        ) as run:
            response = api.ask(api.QueryRequest(query="test question"))

        self.assertEqual(response["status"], "success")
        execute.assert_not_called()
        set_current.assert_called_once_with(DOCUMENT_ID)
        run.assert_called_once_with("test question")

    def test_filename_slug_is_resolved_before_retrieval(self):
        database_result = SimpleNamespace(data=[{"id": DOCUMENT_ID}])
        with patch.object(api, "get_current_ipo", return_value="dhoot transmission limited - ap_p"), patch.object(
            api, "execute_supabase", return_value=database_result
        ), patch.object(api, "set_current_ipo") as set_current, patch.object(
            api, "run", return_value=("answer", 1.0)
        ) as run:
            api.ask(api.QueryRequest(query="test question"))

        set_current.assert_called_once_with(DOCUMENT_ID)
        run.assert_called_once_with("test question")

    def test_multiple_completed_documents_are_rejected(self):
        database_result = SimpleNamespace(data=[{"id": DOCUMENT_ID}, {"id": "another-document"}])
        with patch.object(api, "get_current_ipo", return_value=None), patch.object(
            api, "execute_supabase", return_value=database_result
        ):
            with self.assertRaises(Exception) as raised:
                api.ask(api.QueryRequest(query="test question"))

        self.assertIn("Multiple completed documents", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
