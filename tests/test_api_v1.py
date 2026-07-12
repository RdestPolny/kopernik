import os
import unittest
from unittest.mock import patch

os.environ.setdefault("FIRECRAWL_KEY", "test")
os.environ.setdefault("GEMINI_KEY", "test")

from fastapi.testclient import TestClient

import main


FIXED_RESULT = {
    "url": "https://example.com",
    "scores": {"overall": 74},
    "dashboard": {"overall": 74},
    "synthesis": {"summary": "Test summary"},
    "factor_index": [
        {"factor_id": "factor-1", "score": 3},
        {"factor_id": "factor-2", "score": 2},
        {"factor_id": "factor-3", "score": 1},
    ],
    "page_audits": [
        {"url": "https://example.com", "page_type": "homepage"},
        {"url": "https://example.com/about", "page_type": "about"},
    ],
}


class ApiV1ContractTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        with main._AUDIT_JOBS_LOCK:
            main._AUDIT_JOBS.clear()

    def _create_completed_audit(self) -> str:
        with patch.object(main, "fixed_report_for", return_value=FIXED_RESULT):
            response = self.client.post("/v1/audits", json={"domain": "example.com"})
        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["schema_version"], "1.0")
        return body["audit_id"]

    def test_openapi_exposes_versioned_contract(self):
        schema = self.client.get("/openapi.json").json()
        self.assertEqual(schema["info"]["version"], "1.0")
        self.assertIn("/v1/audits", schema["paths"])
        self.assertIn("/v1/audits/{audit_id}/findings", schema["paths"])

    def test_create_and_read_completed_audit(self):
        audit_id = self._create_completed_audit()
        response = self.client.get(f"/v1/audits/{audit_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "completed")
        self.assertEqual(response.json()["scoring_version"], "2026-07")

    def test_summary_does_not_return_internal_full_result(self):
        audit_id = self._create_completed_audit()
        response = self.client.get(f"/v1/audits/{audit_id}/summary")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["scores"]["overall"], 74)
        self.assertNotIn("factor_index", data)
        self.assertNotIn("page_audits", data)

    def test_findings_and_pages_are_paginated(self):
        audit_id = self._create_completed_audit()
        findings = self.client.get(
            f"/v1/audits/{audit_id}/findings", params={"page": 2, "page_size": 2}
        ).json()
        self.assertEqual(findings["total"], 3)
        self.assertEqual(findings["pages"], 2)
        self.assertEqual(len(findings["items"]), 1)

        pages = self.client.get(
            f"/v1/audits/{audit_id}/pages", params={"page": 1, "page_size": 1}
        ).json()
        self.assertEqual(pages["total"], 2)
        self.assertEqual(pages["pages"], 2)
        self.assertEqual(len(pages["items"]), 1)

    def test_v1_errors_have_stable_envelope(self):
        response = self.client.get("/v1/audits/missing")
        self.assertEqual(response.status_code, 404)
        error = response.json()["error"]
        self.assertEqual(error["code"], "audit_not_found")
        self.assertTrue(error["request_id"])

        invalid = self.client.post("/v1/audits", json={})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json()["error"]["code"], "validation_error")

    def test_legacy_errors_keep_existing_format(self):
        response = self.client.get("/audit/result", params={"job_id": "missing"})
        self.assertEqual(response.status_code, 404)
        self.assertIn("detail", response.json())


if __name__ == "__main__":
    unittest.main()
