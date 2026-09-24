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


def _sms_profile_file(tmp_path, *, allow_terms=True):
    path = tmp_path / "sms-profile.json"
    path.write_text(json.dumps({
        "fields": {
            "identity.full_name": {
                "value": "Example User",
                "confidence": 1.0,
                "user_confirmed": True,
            },
            "identity.phone": {
                "value": "13800138000",
                "confidence": 1.0,
                "user_confirmed": True,
                "sensitive": True,
            },
            "policy.auto_accept_privacy_terms": {
                "value": bool(allow_terms),
                "confidence": 1.0,
                "user_confirmed": True,
            },
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


def test_live_form_write_requires_certified_account_identity(tmp_path, monkeypatch):
    fields = [WebField(field_id="name", selector="#name", label="姓名", required=True)]
    unverified = FakeAdapter(fields, final="Submit application")
    monkeypatch.setattr("executor.application.adapter_for_url", lambda _url: unverified)
    monkeypatch.setattr("executor.application.browser_mode", lambda: "live")
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "applications")
    runner = ApplicationExecutor("https://example.test/apply", _profile_file(tmp_path),
                                 {"deepseek": {"enabled": False}})
    plan = runner.run(max_pages=1)
    assert plan.stage == ApplicationStage.BLOCKED
    assert plan.metadata["auth_kind"] == "account_identity_unverified"
    assert unverified.applied == []
    assert unverified.submit_calls == 0

    verified = FakeAdapter(fields, final="Submit application")
    verified.account_identity_verified = lambda _profile: True
    monkeypatch.setattr("executor.application.adapter_for_url", lambda _url: verified)
    plan = ApplicationExecutor("https://example.test/apply", _profile_file(tmp_path),
                               {"deepseek": {"enabled": False}}).run(max_pages=1)
    assert plan.stage == ApplicationStage.READY_TO_SUBMIT
    assert [item.field_id for item in verified.applied] == ["name"]
    assert verified.submit_calls == 0


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


def test_uncovered_project_blocks_false_ready(tmp_path, monkeypatch):
    profile = tmp_path / "project-profile.json"
    profile.write_text(json.dumps({"fields": {"identity.full_name": {
        "value": "Synthetic Applicant", "confidence": 1.0,
    }}, "collections": {"projects": [{"title": "AI Product Research"}]}}))
    fake = FakeAdapter([
        WebField(field_id="name", selector="#name", label="姓名", required=True),
        WebField(field_id="project", selector="#project", label="项目名称", required=True,
                 current_value="AI Product"),
    ], final="Submit application")
    monkeypatch.setattr("executor.application.adapter_for_url", lambda _url: fake)
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "applications")
    runner = ApplicationExecutor("https://example.test/apply", profile,
                                 {"deepseek": {"enabled": False}})
    # The existing site value is retained, but a prefix title cannot certify
    # the distinct canonical project.
    plan = runner.run(max_pages=1)
    assert plan.stage == ApplicationStage.BLOCKED
    assert plan.metadata["final_review"]["project_coverage"]["uncovered_projects"] == ["AI Product Research"]
    assert fake.submit_calls == 0


def test_unparsed_resume_research_section_blocks_false_ready(tmp_path, monkeypatch):
    profile = tmp_path / "unparsed-profile.json"
    profile.write_text(json.dumps({"fields": {"identity.full_name": {
        "value": "Synthetic Applicant", "confidence": 1.0}},
        "collections": {"projects": [], "resume_project_parse_status": "UNPARSED_SECTION"}}))
    fake = FakeAdapter([WebField(field_id="name", selector="#name", label="姓名",
                                 required=True)], final="Submit application")
    monkeypatch.setattr("executor.application.adapter_for_url", lambda _url: fake)
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "applications")
    plan = ApplicationExecutor("https://example.test/apply", profile,
                               {"deepseek": {"enabled": False}}).run(max_pages=1)
    assert plan.stage == ApplicationStage.BLOCKED
    assert plan.metadata["block_reason"] == "structured project coverage unproven"
    assert plan.metadata["final_review"]["project_coverage"]["status"] == "REVIEW_REQUIRED"
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
    with pytest.raises(RuntimeError, match="screenshots are disabled"):
        store.screenshot_path("ready-to-submit")


def test_legacy_application_cli_rejects_external_execution():
    from executor.app_cli import _require_isolated_fixture

    with pytest.raises(RuntimeError, match="isolated local fixtures only"):
        _require_isolated_fixture("https://careers.example.test/jobs/1")
    _require_isolated_fixture("http://127.0.0.1:8123/apply")


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


def test_unknown_answer_is_bound_to_question_and_page_not_reused_dom_id(tmp_path, monkeypatch):
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "applications")
    runner = ApplicationExecutor(
        "https://example.test/apply", _profile_file(tmp_path),
        {"deepseek": {"enabled": False}},
    )
    first = WebField(field_id="question", selector="#question", label="Favorite project?",
                     required=True, page_url="https://example.test/apply/one")
    unresolved = runner._resolve_page([first])[0]
    assert unresolved.canonical_key.startswith("site_field.")
    runner.user_answers = [{"canonical_key": unresolved.canonical_key,
                            "field_id": unresolved.canonical_key, "value": "Synthetic answer"}]
    assert runner._resolve_page([first])[0].source == "user_execution_answer"
    other_page = first.model_copy(update={"page_url": "https://example.test/apply/two"})
    other_question = first.model_copy(update={"label": "Employment history?"})
    assert runner._resolve_page([other_page])[0].source != "user_execution_answer"
    assert runner._resolve_page([other_question])[0].source != "user_execution_answer"


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
    def __init__(
        self,
        kind,
        *,
        field_status="unique",
        clears=True,
        final=None,
        confirms=False,
        kind_after_confirm=None,
        prepare_status="not_needed",
        kind_after_prepare=None,
    ):
        super().__init__(
            [WebField(field_id="name", selector="#name", label="姓名", required=True)],
            final=final,
        )
        self.kind = kind
        self.field_status = field_status
        self.clears = clears
        self.entered = False
        self.confirms = confirms
        self.kind_after_confirm = kind_after_confirm
        self.confirm_calls = 0
        self.prepare_status = prepare_status
        self.kind_after_prepare = kind_after_prepare
        self.prepare_calls = []

    def auth_challenge_kind(self):
        return self.kind

    def otp_field_status(self):
        return self.field_status

    def current_page_hostname(self):
        return "auth.example.test"

    def prepare_one_time_code_auth(self, phone, *, allow_standard_auth_terms=False):
        self.prepare_calls.append((phone, allow_standard_auth_terms))
        if self.kind_after_prepare is not None:
            self.kind = self.kind_after_prepare
        return self.prepare_status

    def enter_one_time_code(self, code):
        self.entered = bool(code)
        if self.clears:
            self.kind = None
        return self.field_status == "unique"

    def confirm_one_time_code_auth(self):
        self.confirm_calls += 1
        if not self.confirms:
            return False
        self.kind = self.kind_after_confirm
        return True


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
    assert plan.metadata["block_reason"] == "OTP authentication confirmation control unavailable or ambiguous"
    assert adapter.confirm_calls == 1
    assert adapter.submit_calls == 0


def test_unique_otp_auth_confirmation_continues_to_manual_submit_boundary(tmp_path, monkeypatch):
    adapter = AuthAdapter(
        "one_time_code",
        clears=False,
        confirms=True,
        kind_after_confirm=None,
        final="Submit application",
    )
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

    assert adapter.confirm_calls == 1
    assert plan.stage == ApplicationStage.READY_TO_SUBMIT
    assert adapter.submit_calls == 0
    audit_text = (runner.audit.root / "plan.json").read_text(encoding="utf-8")
    audit_text += runner.audit.actions_path.read_text(encoding="utf-8")
    assert "462810" not in audit_text
    assert "otp_authentication_confirmation" in audit_text


@pytest.mark.parametrize("kind", ["password", "captcha", "other"])
def test_challenge_after_otp_confirmation_blocks(tmp_path, monkeypatch, kind):
    adapter = AuthAdapter(
        "one_time_code",
        clears=False,
        confirms=True,
        kind_after_confirm=kind,
    )
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
    assert plan.metadata["block_reason"] == "authentication challenge remains after OTP confirmation"
    assert adapter.confirm_calls == 1
    assert adapter.submit_calls == 0


def test_sms_auth_preparation_uses_local_phone_and_policy_before_relay(tmp_path, monkeypatch):
    adapter = AuthAdapter(
        "one_time_code",
        prepare_status="requested",
        final="Submit application",
    )
    bridge = FakeOtpBridge({"code": "462810", "source": "local_broker"})
    monkeypatch.setattr("executor.application.adapter_for_url", lambda _url: adapter)
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "applications")

    runner = ApplicationExecutor(
        "https://example.test/apply",
        _sms_profile_file(tmp_path, allow_terms=True),
        {"deepseek": {"enabled": False}},
        otp_bridge=bridge,
    )
    plan = runner.run(max_pages=1)

    assert adapter.prepare_calls == [("13800138000", True)]
    assert bridge.sites == ["auth.example.test"]
    assert adapter.entered is True
    assert plan.stage == ApplicationStage.READY_TO_SUBMIT
    actions = runner.audit.actions_path.read_text(encoding="utf-8")
    assert "13800138000" not in actions
    assert "462810" not in actions
    assert "otp_authentication_prepare" in actions


def test_sms_auth_consent_requirement_blocks_before_requesting_relay(tmp_path, monkeypatch):
    adapter = AuthAdapter(
        "one_time_code",
        prepare_status="consent_required",
    )
    bridge = FakeOtpBridge({"code": "462810", "source": "local_broker"})
    monkeypatch.setattr("executor.application.adapter_for_url", lambda _url: adapter)
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "applications")

    plan = ApplicationExecutor(
        "https://example.test/apply",
        _sms_profile_file(tmp_path, allow_terms=False),
        {"deepseek": {"enabled": False}},
        otp_bridge=bridge,
    ).run(max_pages=1)

    assert adapter.prepare_calls == [("13800138000", False)]
    assert bridge.sites == []
    assert adapter.entered is False
    assert plan.stage == ApplicationStage.BLOCKED
    assert plan.metadata["auth_kind"] == "sms_setup"
    assert "consent" in plan.metadata["block_reason"].lower()


def test_sms_auth_missing_canonical_phone_blocks_before_relay(tmp_path, monkeypatch):
    adapter = AuthAdapter(
        "one_time_code",
        prepare_status="phone_required",
    )
    bridge = FakeOtpBridge({"code": "462810", "source": "local_broker"})
    monkeypatch.setattr("executor.application.adapter_for_url", lambda _url: adapter)
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "applications")

    plan = ApplicationExecutor(
        "https://example.test/apply",
        _profile_file(tmp_path),
        {"deepseek": {"enabled": False}},
        otp_bridge=bridge,
    ).run(max_pages=1)

    assert adapter.prepare_calls == [(None, False)]
    assert bridge.sites == []
    assert plan.stage == ApplicationStage.BLOCKED
    assert plan.metadata["auth_kind"] == "sms_setup"


def test_sms_auth_captcha_after_code_request_stops_before_relay(tmp_path, monkeypatch):
    adapter = AuthAdapter(
        "one_time_code",
        prepare_status="requested",
        kind_after_prepare="captcha",
    )
    bridge = FakeOtpBridge({"code": "462810", "source": "local_broker"})
    monkeypatch.setattr("executor.application.adapter_for_url", lambda _url: adapter)
    monkeypatch.setattr("executor.audit.ROOT", tmp_path / "applications")

    plan = ApplicationExecutor(
        "https://example.test/apply",
        _sms_profile_file(tmp_path, allow_terms=True),
        {"deepseek": {"enabled": False}},
        otp_bridge=bridge,
    ).run(max_pages=1)

    assert bridge.sites == []
    assert adapter.entered is False
    assert plan.stage == ApplicationStage.BLOCKED
    assert plan.metadata["auth_kind"] == "captcha"
