"""An independent saved draft, rather than a fill plan, controls READY."""
from __future__ import annotations

import copy
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.request import urlopen

import pytest

from executor.models import ApplicationPlan, FieldResolution, ResolutionStatus
from executor.review_certificate import ReviewUnverified, certify_review, recheck_review


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class DraftService(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        body = json.dumps(self.server.actual).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_server_draft_certificate_and_fault_canaries():
    profile = {"generated_at": "2026-09-24T00:00:00Z", "assets": {}}
    plan = ApplicationPlan(execution_id="task-1",
                           target_url="https://synthetic.example/apply/job-1",
                           site_id="synthetic")
    plan.fields = [FieldResolution(
        field_id="full_name", selector="#name", label="Full name",
        status=ResolutionStatus.RESOLVED, value="Synthetic Applicant",
        required=True, canonical_key="identity.full_name")]
    snapshot = {
        "source": "server_readback",
        "target_sha256": digest(plan.target_url),
        "draft_id_digest": digest("draft-1"),
        "revision": 2,
        "account_verified": True,
        "account_identity_digest": digest("account-1"),
        "complete_pages": True,
        "complete_required": True,
        "save_status": "VERIFIED",
        "validation_error_count": 0,
        "hidden_required_count": 0,
        "unverified_default_count": 0,
        "document_epoch": "page-epoch-1",
        "driver_version": "synthetic-v1",
        "fields": [{"index": 0, "field_id": "full_name", "selector": "#name",
                    "required": True, "value": "Synthetic Applicant"}],
        "attachments": {},
        "rows": {},
    }
    service = ThreadingHTTPServer(("127.0.0.1", 0), DraftService)
    service.actual = snapshot
    Thread(target=service.serve_forever, daemon=True).start()
    try:
        def read():
            with urlopen(f"http://127.0.0.1:{service.server_port}/draft") as response:
                return json.load(response)

        certificate = certify_review(
            profile, plan, read(), expected_account_identity_digest=digest("account-1"))
        safe = certificate.safe_summary()
        assert set(safe["checks"].values()) == {"PASS"}
        assert "Synthetic Applicant" not in json.dumps(safe)
        assert safe["final_click_actor"] == "user"
        assert recheck_review(
            certificate, profile, plan, read(),
            expected_account_identity_digest=digest("account-1")) == certificate

        faults = (
            ("target_sha256", digest("another-job")),
            ("draft_id_digest", digest("another-draft")),
            ("account_verified", False),
            ("account_identity_digest", digest("another-account")),
            ("complete_pages", False),
            ("complete_required", False),
            ("save_status", "UNVERIFIED"),
            ("hidden_required_count", 1),
            ("unverified_default_count", 1),
            ("validation_error_count", 1),
            ("fields", []),
        )
        for key, value in faults:
            service.actual = copy.deepcopy(snapshot)
            service.actual[key] = value
            with pytest.raises(ReviewUnverified):
                certify_review(profile, plan, read(),
                               expected_draft_id_digest=digest("draft-1"),
                               expected_account_identity_digest=digest("account-1"))
        service.actual = copy.deepcopy(snapshot)
        service.actual["fields"][0]["value"] = "Wrong Applicant"
        with pytest.raises(ReviewUnverified):
            certify_review(profile, plan, read(),
                           expected_account_identity_digest=digest("account-1"))

        for key, value in (("revision", 1), ("revision", 3),
                           ("document_epoch", "new-page"),
                           ("driver_version", "synthetic-v2")):
            service.actual = copy.deepcopy(snapshot)
            service.actual[key] = value
            with pytest.raises(ReviewUnverified):
                recheck_review(certificate, profile, plan, read(),
                               expected_account_identity_digest=digest("account-1"))
        service.actual = snapshot
        changed_profile = dict(profile, generated_at="2026-09-24T01:00:00Z")
        with pytest.raises(ReviewUnverified):
            recheck_review(certificate, changed_profile, plan, read(),
                           expected_account_identity_digest=digest("account-1"))
    finally:
        service.shutdown()
        service.server_close()


def test_missing_observer_and_unconfirmed_default_fail_closed():
    profile = {"generated_at": "v1"}
    plan = ApplicationPlan(execution_id="task", target_url="https://example.test/job",
                           site_id="synthetic")
    with pytest.raises(ReviewUnverified):
        certify_review(profile, plan, None)
    plan.fields = [FieldResolution(
        field_id="consent", selector="#consent", label="Consent",
        status=ResolutionStatus.KEEP_EXISTING, value=True, required=True)]
    observation = {
        "source": "server_readback", "target_sha256": digest(plan.target_url),
        "draft_id_digest": digest("draft"), "revision": 1,
        "account_verified": True, "account_identity_digest": digest("account"),
        "complete_pages": True, "complete_required": True, "save_status": "VERIFIED",
        "validation_error_count": 0, "hidden_required_count": 0,
        "unverified_default_count": 0, "document_epoch": "epoch",
        "driver_version": "synthetic-v1",
        "fields": [{"index": 0, "field_id": "consent", "selector": "#consent",
                    "required": True, "value": True}],
        "attachments": {}, "rows": {},
    }
    with pytest.raises(ReviewUnverified, match="unconfirmed site default"):
        certify_review(profile, plan, observation,
                       expected_account_identity_digest=digest("account"))


def test_project_row_identity_and_values_must_match_current_inventory():
    profile = {"generated_at": "v1", "collections": {"projects": [
        {"id": "research-1", "title": "Research One", "category": "research"}]}}
    plan = ApplicationPlan(execution_id="task", target_url="https://example.test/job",
                           site_id="synthetic")
    values_digest = digest(json.dumps({"title": "Research One", "category": "research"},
                                      sort_keys=True, ensure_ascii=False,
                                      separators=(",", ":")))
    snapshot = {
        "source": "server_readback", "target_sha256": digest(plan.target_url),
        "draft_id_digest": digest("draft"), "revision": 2,
        "account_verified": True, "account_identity_digest": digest("account"),
        "complete_pages": True, "complete_required": True, "save_status": "VERIFIED",
        "validation_error_count": 0, "hidden_required_count": 0,
        "unverified_default_count": 0, "document_epoch": "epoch",
        "driver_version": "synthetic-v1", "fields": [], "attachments": {},
        "rows": {"projects": [{"record_id": "research-1",
                              "values_digest": values_digest}]},
    }
    expected = {"projects": {"research-1": values_digest}}
    assert certify_review(
        profile, plan, snapshot, expected_rows=expected,
        expected_account_identity_digest=digest("account")).row_count == 1
    snapshot["rows"]["projects"][0]["values_digest"] = digest("wrong category")
    with pytest.raises(ReviewUnverified, match="structured row values"):
        certify_review(profile, plan, snapshot, expected_rows=expected,
                       expected_account_identity_digest=digest("account"))
