import os
import hashlib
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
        self.api_key = "_".join(("kop", "test", "testkey01", "abcdefghijklmnopqrstuvwxyz123456"))
        self.other_api_key = "_".join(("kop", "test", "otherkey1", "abcdefghijklmnopqrstuvwxyz654321"))
        self.records = {
            "testkey01": {
                "key_hash": hashlib.sha256(self.api_key.encode()).hexdigest(),
                "organization_id": "org-test",
                "environment": "test",
                "scopes": ["audits:create", "audits:read", "usage:read"],
                "revoked": False,
            },
            "otherkey1": {
                "key_hash": hashlib.sha256(self.other_api_key.encode()).hexdigest(),
                "organization_id": "org-other",
                "environment": "test",
                "scopes": ["audits:create", "audits:read", "usage:read"],
                "revoked": False,
            },
        }
        self.auth = {"Authorization": f"Bearer {self.api_key}"}
        self.other_auth = {"Authorization": f"Bearer {self.other_api_key}"}
        self.key_patch = patch.object(
            main, "_load_api_key_record", side_effect=lambda key_id: self.records.get(key_id)
        )
        self.key_patch.start()
        with main._API_KEY_CACHE_LOCK:
            main._API_KEY_CACHE.clear()
        with main._AUDIT_JOBS_LOCK:
            main._AUDIT_JOBS.clear()
        main._BATCHES.clear()
        main._USAGE_MEMORY.clear()

    def tearDown(self):
        self.key_patch.stop()

    def _create_completed_audit(self) -> str:
        with patch.object(main, "fixed_report_for", return_value=FIXED_RESULT):
            response = self.client.post(
                "/v1/audits", json={"domain": "example.com"}, headers=self.auth
            )
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
        self.assertIn("/v1/batches", schema["paths"])
        self.assertIn("/v1/usage", schema["paths"])

    def test_create_and_read_completed_audit(self):
        audit_id = self._create_completed_audit()
        response = self.client.get(f"/v1/audits/{audit_id}", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "completed")
        self.assertEqual(response.json()["scoring_version"], "2026-07")

    def test_summary_does_not_return_internal_full_result(self):
        audit_id = self._create_completed_audit()
        response = self.client.get(f"/v1/audits/{audit_id}/summary", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["scores"]["overall"], 74)
        self.assertNotIn("factor_index", data)
        self.assertNotIn("page_audits", data)

    def test_findings_and_pages_are_paginated(self):
        audit_id = self._create_completed_audit()
        findings = self.client.get(
            f"/v1/audits/{audit_id}/findings",
            params={"page": 2, "page_size": 2},
            headers=self.auth,
        ).json()
        self.assertEqual(findings["total"], 3)
        self.assertEqual(findings["pages"], 2)
        self.assertEqual(len(findings["items"]), 1)

        pages = self.client.get(
            f"/v1/audits/{audit_id}/pages",
            params={"page": 1, "page_size": 1},
            headers=self.auth,
        ).json()
        self.assertEqual(pages["total"], 2)
        self.assertEqual(pages["pages"], 2)
        self.assertEqual(len(pages["items"]), 1)

    def test_v1_errors_have_stable_envelope(self):
        response = self.client.get("/v1/audits/missing", headers=self.auth)
        self.assertEqual(response.status_code, 404)
        error = response.json()["error"]
        self.assertEqual(error["code"], "audit_not_found")
        self.assertTrue(error["request_id"])

        invalid = self.client.post("/v1/audits", json={}, headers=self.auth)
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json()["error"]["code"], "validation_error")

    def test_legacy_errors_keep_existing_format(self):
        response = self.client.get("/audit/result", params={"job_id": "missing"})
        self.assertEqual(response.status_code, 404)
        self.assertIn("detail", response.json())

    def test_authentication_is_required(self):
        response = self.client.get("/v1/capabilities")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "missing_api_key")
        self.assertEqual(response.headers["www-authenticate"], "Bearer")

    def test_audit_is_hidden_from_another_organization(self):
        audit_id = self._create_completed_audit()
        response = self.client.get(f"/v1/audits/{audit_id}", headers=self.other_auth)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "audit_not_found")
        legacy = self.client.get("/audit/result", params={"job_id": audit_id})
        self.assertEqual(legacy.status_code, 404)

    def test_scope_is_enforced(self):
        self.records["testkey01"]["scopes"] = ["audits:read"]
        with main._API_KEY_CACHE_LOCK:
            main._API_KEY_CACHE.clear()
        response = self.client.post(
            "/v1/audits", json={"domain": "example.com"}, headers=self.auth
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "insufficient_scope")

    def test_revoked_key_is_rejected(self):
        self.records["testkey01"]["revoked"] = True
        with main._API_KEY_CACHE_LOCK:
            main._API_KEY_CACHE.clear()
        response = self.client.get("/v1/capabilities", headers=self.auth)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "invalid_api_key")

    def test_identity_returns_organization_and_scopes(self):
        response = self.client.get("/v1/me", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["organization_id"], "org-test")
        self.assertIn("audits:read", response.json()["scopes"])

    def test_organization_report_is_not_saved_under_public_domain_key(self):
        with main._REPORTS_LOCK:
            main._REPORTS_MEMORY.clear()
        with patch.object(main, "_save_report_to_firestore", return_value=True):
            key = main.save_report(FIXED_RESULT, organization_id="org-test")
        self.assertTrue(key.startswith("__org__"))
        with main._REPORTS_LOCK:
            self.assertIn(key, main._REPORTS_MEMORY)
            self.assertNotIn("example.com", main._REPORTS_MEMORY)
        public = self.client.get("/report", params={"domain": key})
        self.assertEqual(public.status_code, 404)

    def test_private_network_target_is_rejected(self):
        with patch("main.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            response = self.client.post(
                "/v1/audits", json={"domain": "internal.example"}, headers=self.auth
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_target")

    def test_batch_accepts_multiple_domains(self):
        with patch.object(main, "fixed_report_for", return_value=FIXED_RESULT):
            response = self.client.post(
                "/v1/batches",
                json={"domains": ["example.com", "example.org"]},
                headers={**self.auth, "Idempotency-Key": "batch-test-001"},
            )
        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["completed"], 2)

    def test_idempotency_returns_same_audit(self):
        headers = {**self.auth, "Idempotency-Key": "audit-test-001"}
        with patch.object(main, "fixed_report_for", return_value=FIXED_RESULT):
            first = self.client.post("/v1/audits", json={"domain": "example.com"}, headers=headers)
            second = self.client.post("/v1/audits", json={"domain": "example.com"}, headers=headers)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json()["audit_id"], second.json()["audit_id"])

    def test_idempotency_rejects_different_domain(self):
        headers = {**self.auth, "Idempotency-Key": "audit-conflict-001"}
        with patch.object(main, "fixed_report_for", return_value=FIXED_RESULT):
            first = self.client.post("/v1/audits", json={"domain": "example.com"}, headers=headers)
            second = self.client.post("/v1/audits", json={"domain": "example.org"}, headers=headers)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["error"]["code"], "idempotency_conflict")

    def test_job_can_be_restored_from_persistent_storage(self):
        persisted = {
            "organization_id": "org-test",
            "url": "https://example.com",
            "status": "done",
            "pct": 100,
            "message": "done",
            "created_at": 1.0,
            "finished_at": 2.0,
            "updated_at": 2.0,
            "result": FIXED_RESULT,
        }
        with patch.object(main, "_load_audit_job_firestore", return_value=persisted):
            response = self.client.get("/v1/audits/persisted-job", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "completed")

    def test_usage_reports_completed_audits_without_enforcing_quota(self):
        self._create_completed_audit()
        response = self.client.get("/v1/usage", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["completed"], 1)


if __name__ == "__main__":
    unittest.main()
