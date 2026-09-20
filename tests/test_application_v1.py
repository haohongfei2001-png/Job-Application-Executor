import json

import pytest

from executor.application import ApplicationExecutor
from executor.models import (
    ApplicationStage,
    SubmissionVerification,
    ValidationResult,
    WebField,
)
from executor.otp.bridge import OtpBridgeError


def _profile_file(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({
        "fields": {
            "identity.full_name": {
                "value": "Example User",
                "confidence": 1.0,
                "user_confirmed": True,
            }
        }
    }), encoding="utf-8")
    return path


class FakeAdapter:
    def __init__(self, fields, final=None, verification_level="page_signal"):
        self.fields = fields
        self.final = final
        self.applied = []
        self.verification_level = verification_level
        self.submit_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def auth_challenge(self):
        return False

    def start_application(self):
        return False

    def discover_fields(self):
        return self.fields

    def apply_resolutions(self, resolutions):
        resolved = [x for x in resolutions if str(x.status) == "RESOLVED"]
        self.applied.extend(resolved)
        return [{"field_id": x.field_id, "ok": True} for x in resolved]

    def validate(self, plan):
        return ValidationResult(ok=True)

    def final_submit_control(self):
        return self.final

    def advance(self):
        return False

    def submit(self):
        self.submit_calls += 1
        return {"control": self.final, "url": "https://example.test/done"}

    def verify_submission(self):
        return SubmissionVerification(
            verified=True,
            level=self.verification_level,
            status="submitted",
        )

    def screenshot(self, path):
        return None


def test_deterministic_fields_are_filled_before_required_question(tmp_path, monkeypatch):
    fields = [
        WebField(field_id="name", selector="#name", label="姓名", required=True),
        WebField(field_id="salary", selector="#salary", label="Expected salary", required=True),
    ]
    fake = FakeAdapter(fields)
    monkeypatch.setattr("executor.application.adapter_for_url", lambda _url: fake)
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "applications")
    runner = ApplicationExecutor(
        "https://example.test/apply", _profile_file(tmp_path),
        {"deepseek": {"enabled": False}},
    )
    plan = runner.run(max_pages=1)
    assert plan.stage == ApplicationStage.BLOCKED
    assert [x.field_id for x in fake.applied] == ["name"]
    assert any(x.field_id == "salary" for x in plan.unresolved_fields)


def test_ready_to_submit_does_not_submit_without_authorization(tmp_path, monkeypatch):
    fake = FakeAdapter([
        WebField(field_id="name", selector="#name", label="姓名", required=True),
    ], final="Submit application")
    monkeypatch.setattr("executor.application.adapter_for_url", lambda _url: fake)
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "applications")
    runner = ApplicationExecutor(
        "https://example.test/apply", _profile_file(tmp_path),
        {"deepseek": {"enabled": False}},
    )
    plan = runner.run(max_pages=1)
    assert plan.stage == ApplicationStage.READY_TO_SUBMIT
    assert fake.submit_calls == 0


def test_submit_authorized_still_requires_manual_final_click(tmp_path, monkeypatch):
    fake = FakeAdapter([
        WebField(field_id="name", selector="#name", label="姓名", required=True),
    ], final="Submit application", verification_level="page_signal")
    monkeypatch.setattr("executor.application.adapter_for_url", lambda _url: fake)
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "applications")
    runner = ApplicationExecutor(
        "https://example.test/apply", _profile_file(tmp_path),
        {"deepseek": {"enabled": False}}, submit_authorized=True,
    )
    plan = runner.run(max_pages=1)
    assert plan.stage == ApplicationStage.READY_TO_SUBMIT
    assert fake.submit_calls == 0
    assert plan.metadata["manual_final_click_required"] is True
    assert plan.metadata["submit_authorized_does_not_allow_final_click"] is True
    assert plan.metadata["final_review"]["human_review_required"] is True
    assert plan.metadata["final_review"]["final_click_actor"] == "user"


def test_recovery_of_terminal_execution_is_read_only(tmp_path, monkeypatch):
    from executor.audit import AuditStore
    from executor.models import ApplicationPlan
    from executor.recovery import recover_execution

    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "applications")
    plan = ApplicationPlan(
        execution_id="terminal-fixture",
        target_url="https://example.test/apply",
        site_id="generic_web",
        stage=ApplicationStage.SUBMITTED,
    )
    AuditStore(plan.execution_id).save_plan(plan)
    recovered = recover_execution(plan.execution_id, tmp_path / "missing-profile.json")
    assert recovered.stage == ApplicationStage.SUBMITTED


def test_audit_redacts_sensitive_payloads(tmp_path, monkeypatch):
    from executor.audit import AuditStore

    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "applications")
    store = AuditStore("redaction-fixture")
    store.record_action({
        "type": "probe",
        "id_number": "123456789012345678",
        "code": "462810",
        "ok": True,
    })
    store.save_receipt({"status": "ok", "authorization": "secret-token"})
    assert "123456789012345678" not in store.actions_path.read_text(encoding="utf-8")
    assert "462810" not in store.actions_path.read_text(encoding="utf-8")
    assert "secret-token" not in (store.root / "submit-receipt.json").read_text(encoding="utf-8")


def test_optional_legal_or_subjective_question_still_blocks(tmp_path, monkeypatch):
    fields = [
        WebField(field_id="name", selector="#name", label="姓名", required=True),
        WebField(field_id="declaration", selector="#declaration", label="I certify this is accurate", required=False),
    ]
    fake = FakeAdapter(fields)
    monkeypatch.setattr("executor.application.adapter_for_url", lambda _url: fake)
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "applications")
    runner = ApplicationExecutor(
        "https://example.test/apply", _profile_file(tmp_path),
        {"deepseek": {"enabled": False}},
    )
    plan = runner.run(max_pages=1)
    assert plan.stage == ApplicationStage.BLOCKED
    assert any(x.field_id == "declaration" for x in plan.unresolved_fields)


def test_execution_answer_resolves_same_execution_without_profile_mutation(tmp_path, monkeypatch):
    from executor.audit import AuditStore

    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "applications")
    profile_path = _profile_file(tmp_path)
    execution_id = "answer-fixture"
    store = AuditStore(execution_id)
    store.add_user_answer({
        "field_id": "salary",
        "selector": "#salary",
        "label": "Expected salary",
        "canonical_key": None,
        "value": "negotiable",
        "scope": "execution",
    })

    fake = FakeAdapter([
        WebField(field_id="name", selector="#name", label="姓名", required=True),
        WebField(field_id="salary", selector="#salary", label="Expected salary", required=True),
    ], final="Submit application")
    monkeypatch.setattr("executor.application.adapter_for_url", lambda _url: fake)

    runner = ApplicationExecutor(
        "https://example.test/apply",
        profile_path,
        {"deepseek": {"enabled": False}},
        execution_id=execution_id,
    )
    plan = runner.run(max_pages=1)
    assert plan.stage == ApplicationStage.READY_TO_SUBMIT
    assert fake.submit_calls == 0
    answer = next(x for x in plan.fields if x.field_id == "salary")
    assert answer.source == "user_execution_answer"
    assert json.loads(profile_path.read_text())["fields"].get("preferences.salary_policy") is None


class FakeOtpBridge:
    enabled = True

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.sites = []

    def wait_for_code(self, site):
        self.sites.append(site)
        if self.error:
            raise self.error
        return self.result


class AuthAdapter(FakeAdapter):
    def __init__(self, kind, *, field_status="unique", clears=True, final=None):
        super().__init__(
            [WebField(field_id="name", selector="#name", label="姓名", required=True)],
            final=final,
        )
        self.kind = kind
        self.field_status = field_status
        self.clears = clears
        self.entered = False

    def auth_challenge_kind(self):
        return self.kind

    def otp_field_status(self):
        return self.field_status

    def current_page_hostname(self):
        return "auth.example.test"

    def enter_one_time_code(self, code):
        self.entered = bool(code)
        if self.clears:
            self.kind = None
        return self.field_status == "unique"


def test_relay_success_continues_to_manual_submit_boundary(tmp_path, monkeypatch):
    adapter = AuthAdapter("one_time_code", final="Submit application")
    bridge = FakeOtpBridge({"code": "462810", "source": "iphone_relay"})
    monkeypatch.setattr("executor.application.adapter_for_url", lambda _url: adapter)
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "applications")

    runner = ApplicationExecutor(
        "https://example.test/apply",
        _profile_file(tmp_path),
        {"deepseek": {"enabled": False}},
        otp_bridge=bridge,
    )
    plan = runner.run(max_pages=1)

    assert bridge.sites == ["auth.example.test"]
    assert adapter.entered is True
    assert plan.stage == ApplicationStage.READY_TO_SUBMIT
    assert adapter.submit_calls == 0
    audit_text = (runner.audit.root / "plan.json").read_text(encoding="utf-8")
    audit_text += runner.audit.actions_path.read_text(encoding="utf-8")
    assert "462810" not in audit_text
    assert "iphone_relay" in audit_text


def test_ambiguous_otp_field_blocks_before_relay_request(tmp_path, monkeypatch):
    adapter = AuthAdapter("one_time_code", field_status="ambiguous")
    bridge = FakeOtpBridge({"code": "462810", "source": "iphone_relay"})
    monkeypatch.setattr("executor.application.adapter_for_url", lambda _url: adapter)
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "applications")

    plan = ApplicationExecutor(
        "https://example.test/apply",
        _profile_file(tmp_path),
        {"deepseek": {"enabled": False}},
        otp_bridge=bridge,
    ).run(max_pages=1)

    assert plan.stage == ApplicationStage.BLOCKED
    assert plan.metadata["auth_kind"] == "one_time_code"
    assert bridge.sites == []
    assert adapter.entered is False


@pytest.mark.parametrize(
    ("result", "error", "expected_reason"),
    [
        (None, None, "OTP relay timed out"),
        (None, OtpBridgeError("synthetic relay failure"), "OTP relay error"),
    ],
)
def test_otp_relay_timeout_or_error_blocks(tmp_path, monkeypatch, result, error, expected_reason):
    adapter = AuthAdapter("one_time_code")
    bridge = FakeOtpBridge(result, error)
    monkeypatch.setattr("executor.application.adapter_for_url", lambda _url: adapter)
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "applications")

    plan = ApplicationExecutor(
        "https://example.test/apply",
        _profile_file(tmp_path),
        {"deepseek": {"enabled": False}},
        otp_bridge=bridge,
    ).run(max_pages=1)

    assert plan.stage == ApplicationStage.BLOCKED
    assert plan.metadata["block_reason"] == expected_reason
    assert adapter.entered is False


@pytest.mark.parametrize("kind", ["password", "captcha", "other"])
def test_non_otp_authentication_remains_blocked(tmp_path, monkeypatch, kind):
    adapter = AuthAdapter(kind)
    bridge = FakeOtpBridge({"code": "462810", "source": "iphone_relay"})
    monkeypatch.setattr("executor.application.adapter_for_url", lambda _url: adapter)
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "applications")

    plan = ApplicationExecutor(
        "https://example.test/apply",
        _profile_file(tmp_path),
        {"deepseek": {"enabled": False}},
        otp_bridge=bridge,
    ).run(max_pages=1)

    assert plan.stage == ApplicationStage.BLOCKED
    assert plan.metadata["auth_kind"] == kind
    assert bridge.sites == []
    assert adapter.entered is False


def test_otp_challenge_remaining_after_entry_blocks_without_click(tmp_path, monkeypatch):
    adapter = AuthAdapter("one_time_code", clears=False)
    bridge = FakeOtpBridge({"code": "462810", "source": "iphone_relay"})
    monkeypatch.setattr("executor.application.adapter_for_url", lambda _url: adapter)
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "applications")

    plan = ApplicationExecutor(
        "https://example.test/apply",
        _profile_file(tmp_path),
        {"deepseek": {"enabled": False}},
        otp_bridge=bridge,
    ).run(max_pages=1)

    assert plan.stage == ApplicationStage.BLOCKED
    assert plan.metadata["block_reason"] == "authentication challenge remains after OTP entry"
    assert adapter.submit_calls == 0
