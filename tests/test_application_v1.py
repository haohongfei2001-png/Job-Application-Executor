import json

from executor.application import ApplicationExecutor
from executor.models import (
    ApplicationStage,
    SubmissionVerification,
    ValidationResult,
    WebField,
)


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
    store.record_action({"type": "probe", "id_number": "123456789012345678", "ok": True})
    store.save_receipt({"status": "ok", "authorization": "secret-token"})
    assert "123456789012345678" not in store.actions_path.read_text(encoding="utf-8")
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
