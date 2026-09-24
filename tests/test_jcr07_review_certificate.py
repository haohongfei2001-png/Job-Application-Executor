"""An independent saved draft, rather than a fill plan, controls READY."""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.request import urlopen

import pytest

from executor.audit import safe_plan
from executor.autonomy.worker import Worker
from executor import browser
from executor.adapters.generic_web import GenericWebAdapter
from executor.models import ApplicationPlan, FieldResolution, ResolutionStatus
from executor.review_certificate import ReviewUnverified, certify_review, recheck_review
from executor.protected_targets import load_protected_targets, protected_target, register_user_confirmed_target


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
        required=True, canonical_key="identity.full_name",
        scope_sha256=digest("applicant-field-scope"))]
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
        "document_epoch": "CANARY_PRIVATE_SESSION_EPOCH",
        "driver_version": "synthetic-v1",
        "fields": [{"index": 0, "field_id": "full_name", "selector": "#name",
                    "scope_sha256": digest("applicant-field-scope"),
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
        assert "CANARY_PRIVATE_SESSION_EPOCH" not in json.dumps(safe)
        assert safe["document_epoch_sha256"] == digest("CANARY_PRIVATE_SESSION_EPOCH")
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

        for field_change in ({"index": False},
                             {"scope_sha256": digest("wrong-field-scope")},
                             {"scope_sha256": None}):
            service.actual = copy.deepcopy(snapshot)
            service.actual["fields"][0].update(field_change)
            with pytest.raises(ReviewUnverified, match="current draft field"):
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
        changed_plan = plan.model_copy(deep=True)
        changed_plan.fields[0].value = "Corrected Applicant"
        service.actual = copy.deepcopy(snapshot)
        service.actual["fields"][0]["value"] = "Corrected Applicant"
        with pytest.raises(ReviewUnverified, match="dependencies changed"):
            recheck_review(certificate, profile, changed_plan, read(),
                           expected_account_identity_digest=digest("account-1"))
        service.actual = snapshot
        changed_profile = dict(profile, generated_at="2026-09-24T01:00:00Z")
        with pytest.raises(ReviewUnverified):
            recheck_review(certificate, changed_profile, plan, read(),
                           expected_account_identity_digest=digest("account-1"))
    finally:
        service.shutdown()
        service.server_close()



def test_live_private_review_rechecks_saved_draft_and_invalidates_after_edit(
        tmp_path, monkeypatch):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text('{"generated_at":"v1"}', encoding="utf-8")
    version = hashlib.sha256(profile_path.read_bytes()).hexdigest()
    profile = {"generated_at": "v1", "assets": {}}
    plan = ApplicationPlan(
        execution_id="task", target_url="https://synthetic.example/apply/job-1",
        site_id="synthetic",
        fields=[FieldResolution(
            field_id="name", selector="#name", label="Name",
            status=ResolutionStatus.RESOLVED, value="Original Applicant",
            required=True)],
    )
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
        "document_epoch": "epoch-1",
        "driver_version": "synthetic-v1",
        "fields": [{"index": 0, "field_id": "name", "selector": "#name",
                    "required": True, "value": "Original Applicant"}],
        "attachments": {},
        "rows": {},
    }
    certificate = certify_review(
        profile, plan, snapshot, profile_version=version,
        expected_account_identity_digest=digest("account-1"))
    worker = object.__new__(Worker)
    worker.review_lock = threading.RLock()
    worker.review_cache = {"task": {
        "revision": 7, "expires_at": time.monotonic() + 60,
        "profile_path": profile_path, "profile_version": version,
        "payload": {"source": "last_certified_server_readback"},
        "recheck": {
            "certificate": certificate, "profile": profile, "plan": plan,
            "account": digest("account-1"), "rows": {}, "attachments": {},
        },
    }}
    actual = copy.deepcopy(snapshot)
    monkeypatch.setattr(
        browser, "observe_bound_review",
        lambda target_url, binding, reviewed_plan: copy.deepcopy(actual))
    assert worker.recheck_private_review("task", 7, {"target_id": "bound"})[
        "fresh_rechecked_at_open"] is True
    actual["fields"][0]["value"] = "Edited After READY"
    assert worker.recheck_private_review("task", 7, {"target_id": "bound"}) is None
    assert worker.private_review_available("task", 7) is False


def test_persisted_review_omits_private_project_titles_and_file_names():
    plan = ApplicationPlan(
        execution_id="synthetic-review", target_url="https://example.test/apply",
        site_id="synthetic", attachments={
            "resume": "/private/CANARY_APPLICANT_RESUME.pdf"})
    plan.metadata["final_review"] = {
        "human_review_required": True,
        "final_click_actor": "user",
        "attachment_basenames": {"resume": "CANARY_APPLICANT_RESUME.pdf"},
        "project_coverage": {
            "status": "REVIEW_REQUIRED", "canonical_count": 1,
            "structured_count": 0,
            "canonical_projects": ["CANARY_PRIVATE_PROJECT"],
            "uncovered_projects": ["CANARY_PRIVATE_PROJECT"],
            "explicit_exclusions": [],
        },
    }
    saved = safe_plan(plan)
    text = json.dumps(saved)
    assert "CANARY_PRIVATE_PROJECT" not in text
    assert "CANARY_APPLICANT_RESUME" not in text
    assert saved["metadata"]["final_review"]["project_coverage"][
        "uncovered_count"] == 1
    assert saved["metadata"]["final_review"]["attachment_count"] == 1
    assert saved["attachments"] == {"resume": "[LOCAL_FILE]"}


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
    observation["fields"][0]["default_confirmed"] = True
    observation["fields"][0]["value"] = 1
    with pytest.raises(ReviewUnverified, match="current draft field"):
        certify_review(profile, plan, observation,
                       expected_account_identity_digest=digest("account"))

    # A stale unresolved_fields list must not hide a mandatory field requiring input.
    observation["fields"][0]["value"] = True
    plan.fields[0].status = ResolutionStatus.USER_CONFIRMATION
    assert plan.unresolved_fields == []
    with pytest.raises(ReviewUnverified, match="unresolved application fields"):
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



def test_attachment_receipt_must_match_canonical_asset_hash():
    profile = {"generated_at": "v1", "assets": {
        "resume": {"sha256": digest("canonical-resume")}}}
    plan = ApplicationPlan(
        execution_id="attachment-review",
        target_url="https://example.test/apply",
        site_id="synthetic",
        attachments={"resume": "/private/CANARY_RESUME.pdf"},
        fields=[FieldResolution(
            field_id="resume", selector="#resume", label="Resume",
            canonical_key="assets.resume", status=ResolutionStatus.RESOLVED,
            value="/private/CANARY_RESUME.pdf")],
    )
    snapshot = {
        "source": "server_readback",
        "target_sha256": digest(plan.target_url),
        "draft_id_digest": digest("attachment-draft"),
        "revision": 2,
        "account_verified": True,
        "account_identity_digest": digest("account"),
        "complete_pages": True,
        "complete_required": True,
        "save_status": "VERIFIED",
        "validation_error_count": 0,
        "hidden_required_count": 0,
        "unverified_default_count": 0,
        "document_epoch": "attachment-page",
        "driver_version": "synthetic-v1",
        "fields": [],
        "attachments": {"resume": digest("wrong-file")},
        "rows": {},
    }
    with pytest.raises(ReviewUnverified, match="attachment readback"):
        certify_review(profile, plan, snapshot,
                       expected_account_identity_digest=digest("account"))
    snapshot["attachments"]["resume"] = digest("canonical-resume")
    certificate = certify_review(
        profile, plan, snapshot,
        expected_account_identity_digest=digest("account"))
    assert certificate.attachment_count == 1
    assert "CANARY_RESUME" not in json.dumps(certificate.safe_summary())
    # A requested attachment cannot disappear from the observed form and
    # quietly become an empty receipt inventory.
    plan.fields = []
    snapshot["attachments"] = {}
    with pytest.raises(ReviewUnverified, match="attachment readback"):
        certify_review(profile, plan, snapshot,
                       expected_account_identity_digest=digest("account"))



@pytest.mark.parametrize("human_trigger", ["click", "enter"])
def test_continue_that_submits_remains_human_only(tmp_path, human_trigger):
    html = tmp_path / "ambiguous-continue.html"
    html.write_text(
        """<!doctype html><meta charset="utf-8"><body>
        <form onsubmit="window.submitCount++; document.querySelector('#result').textContent='Application has been submitted'; event.preventDefault()">
          <label>Name <input id="name" value="Synthetic Applicant"></label>
          <button id="continue" type="submit">Continue</button>
        </form>
        <p id="result"></p>
        <script>window.submitCount = 0</script>
        </body>""",
        encoding="utf-8",
    )
    with GenericWebAdapter(html.as_uri()) as adapter:
        assert adapter.next_control() is True
        assert adapter.advance() is False
        with pytest.raises(RuntimeError, match="user click required"):
            adapter.submit()
        assert adapter.page.evaluate("window.submitCount") == 0
        if human_trigger == "click":
            adapter.page.locator("#continue").click()
        else:
            adapter.page.locator("#name").focus()
            adapter.page.keyboard.press("Enter")
        assert adapter.page.evaluate("window.submitCount") == 1
        observed = adapter.verify_submission()
        assert observed.level == "page_signal"
        assert observed.verified is False
        assert observed.status == "page_signal_observed"
        assert observed.evidence == {"success_text_present": True}


def test_post_human_submit_observer_is_read_only_and_page_signal_only(monkeypatch):
    target = "https://jobs.example.test/apply?postId=role-1"
    binding = {
        "process_epoch": "process12345",
        "target_id": "target12345",
        "document_epoch": "100.0",
    }
    calls = {"connect": 0, "stop": 0}

    class FakeBody:
        def inner_text(self, timeout):
            assert timeout == 5000
            return "Application has been submitted for Synthetic Applicant"

    class FakePage:
        def locator(self, selector):
            assert selector == "body"
            return FakeBody()

    class FakePlaywright:
        def stop(self):
            calls["stop"] += 1

    def connect(url, *, existing_only, task_binding, session_epoch):
        calls["connect"] += 1
        assert url == target
        assert existing_only is True
        assert task_binding == {
            "process_epoch": "process12345",
            "target_id": "target12345",
        }
        assert session_epoch == "process12345"
        return FakePlaywright(), object(), object(), FakePage()

    monkeypatch.setattr(browser, "browser_mode", lambda: "live")
    monkeypatch.setattr(browser, "owned_cdp_fingerprint", lambda: "process12345")
    monkeypatch.setattr(browser, "connect", connect)
    monkeypatch.setattr(browser, "page_target_id", lambda _ctx, _page: "target12345")
    monkeypatch.setattr(browser, "page_document_epoch", lambda _page: "200.0")

    observed = browser.observe_bound_submission(target, binding)
    assert observed == {
        "status": "PAGE_SIGNAL_OBSERVED",
        "level": "page_signal",
        "server_verified": False,
        "user_confirmed": False,
        "document_changed": True,
        "replay_allowed": False,
    }
    assert calls == {"connect": 1, "stop": 1}
    assert "Synthetic Applicant" not in json.dumps(observed)
    assert "postId" not in json.dumps(observed)

    monkeypatch.setattr(browser, "owned_cdp_fingerprint", lambda: "otherprocess")
    assert browser.observe_bound_submission(target, binding)["status"] == "PROCESS_EPOCH_CHANGED"
    assert calls == {"connect": 1, "stop": 1}


def test_user_confirmed_protection_is_private_exact_and_idempotent(tmp_path):
    target = "https://jobs.example.test/apply?postId=role-1&resumeId=private-123"
    registry = tmp_path / "private" / "protected-targets.json"
    args = {
        "tenant": "synthetic-tenant",
        "job_id": "role-1",
        "campaign": "autumn",
        "verified_identity": True,
        "path": registry,
    }
    with pytest.raises(ValueError, match="explicit human confirmation"):
        register_user_confirmed_target(target, user_confirmed=False, **args)
    assert not registry.exists()
    with pytest.raises(ValueError, match="verified target"):
        register_user_confirmed_target(
            target, user_confirmed=True, **{**args, "verified_identity": False})
    assert register_user_confirmed_target(target, user_confirmed=True, **args) is True
    assert register_user_confirmed_target(target, user_confirmed=True, **args) is False
    raw = registry.read_text(encoding="utf-8")
    assert target not in raw
    assert "private-123" not in raw
    assert registry.stat().st_mode & 0o077 == 0
    items = load_protected_targets(registry)
    assert protected_target(
        "https://jobs.example.test/new-path?jobId=role-1",
        items, tenant="synthetic-tenant", job_id="role-1", campaign="autumn",
    )["status"] == "USER_CONFIRMED"
    assert protected_target(
        "https://jobs.example.test/new-path?jobId=other",
        items, tenant="synthetic-tenant", job_id="other", campaign="autumn",
    ) is None
