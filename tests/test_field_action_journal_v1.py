"""Value-free field intents retain uncertainty; DOM readback is not server proof."""
import hashlib
import json

import pytest

from executor.adapters.generic_web import GenericWebAdapter
from executor.application import ApplicationExecutor
from executor.autonomy.queue import TaskQueue, TaskSpec
from executor.autonomy.worker import OperationalAudit
from executor.browser import BrowserOwnershipError
from executor.models import FieldResolution, ResolutionStatus


def _owned(tmp_path, *, clock=None):
    ticks = [100.0] if clock is None else clock
    queue = TaskQueue(tmp_path / "runtime", clock=lambda: ticks[0])
    task = queue.enqueue(TaskSpec(company="Synthetic", role="Engineer",
        target_url="https://example.invalid/jobs/field-intent",
        profile_ref=str(tmp_path / "profile.json")))
    claimed = queue.claim("synthetic-worker", lease_seconds=10)
    attempt = queue.begin_run_attempt(task["task_id"], claimed["owner"])
    return queue, task["task_id"], claimed["owner"], attempt, ticks


def test_interrupted_field_survives_restart_and_refuses_run_return_and_replay(tmp_path):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    digest = hashlib.sha256(b"synthetic-control").hexdigest()
    action = queue.begin_field_action(attempt, digest)
    rebuilt = TaskQueue(queue.root, clock=lambda: clock[0])
    assert rebuilt.field_actions(tid)[0]["action_id"] == action
    assert rebuilt.field_actions(tid)[0]["outcome"] == "ATTEMPTED"
    with pytest.raises(RuntimeError, match="reconciliation"):
        rebuilt.require_field_action_readbacks(attempt)
    with pytest.raises(RuntimeError, match="reconciliation"):
        rebuilt.finish_run_attempt(attempt, "RETURNED_UNVERIFIED")
    with pytest.raises(RuntimeError, match="reconciliation"):
        rebuilt.begin_field_action(attempt, hashlib.sha256(b"other").hexdigest())
    clock[0] += 11
    assert rebuilt.claim("replacement-worker") is None
    assert rebuilt.get(tid)["blocker"] == "unknown_outcome"
    assert rebuilt.run_attempts(tid)[0]["outcome"] == "UNKNOWN_OUTCOME"
    assert rebuilt.field_actions(tid)[0]["outcome"] == "ATTEMPTED"
    with pytest.raises(RuntimeError, match="lease lost"):
        rebuilt.finish_field_action(action, "DOM_READBACK_UNVERIFIED")
    rebuilt.finish_field_action(action, "UNKNOWN_OUTCOME")
    assert rebuilt.field_actions(tid)[0]["outcome"] == "UNKNOWN_OUTCOME"


def test_field_readback_cannot_authorize_repeat_or_server_certification(tmp_path):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    digest = hashlib.sha256(b"same-control").hexdigest()
    action = queue.begin_field_action(attempt, digest)
    for unsupported in ("SERVER_VERIFIED", "READY_TO_SUBMIT", "RETURNED_UNVERIFIED"):
        with pytest.raises(ValueError):
            queue.finish_field_action(action, unsupported)
    queue.finish_field_action(action, "DOM_READBACK_UNVERIFIED")
    queue.finish_field_action(action, "DOM_READBACK_UNVERIFIED")
    with pytest.raises(RuntimeError, match="reconciliation"):
        queue.begin_field_action(attempt, digest)
    with pytest.raises(RuntimeError, match="conflict"):
        queue.finish_field_action(action, "UNKNOWN_OUTCOME")
    queue.require_field_action_readbacks(attempt)
    queue.finish_run_attempt(attempt, "RETURNED_UNVERIFIED")
    assert queue.field_actions(tid)[0]["outcome"] == "DOM_READBACK_UNVERIFIED"
    assert queue.get(tid)["stage"] != "READY_TO_SUBMIT"


@pytest.mark.parametrize("digest", [None, True, "PRIVATE_FIELD_CANARY", "f" * 63, "F" * 64])
def test_arbitrary_control_text_never_enters_field_journal(tmp_path, digest):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    with pytest.raises(ValueError):
        queue.begin_field_action(attempt, digest)
    assert queue.field_actions(tid) == []


def _audit(queue, tid, owner, attempt):
    def guard():
        task = queue.get(tid)
        if task["owner"] != owner or task["lease_until"] <= queue.clock():
            raise RuntimeError("lease lost")
    audit = OperationalAudit(queue.root, tid, lambda *_args, **_kwargs: None, guard)
    audit.bind_run_attempt(queue, attempt)
    return audit


def test_real_field_intent_is_committed_before_input_and_unknown_stops_next_field(tmp_path):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    html = tmp_path / "fault.html"
    html.write_text("""<!doctype html><body>
      <label>Private label<input id="first" oninput="this.value=''"></label>
      <label>Other label<input id="second"></label>
      <button type="button" onclick="window.submits=(window.submits||0)+1">Submit application</button>
    """, encoding="utf-8")
    audit = _audit(queue, tid, owner, attempt)
    with GenericWebAdapter(html.as_uri()) as adapter:
        intents = []
        def before(field_id, selector, *, document_epoch):
            assert adapter.page.locator(selector).input_value() == ""
            action = audit.begin_field_action(field_id, selector, document_epoch=document_epoch)
            fresh = TaskQueue(queue.root, clock=lambda: clock[0])
            assert fresh.field_actions(tid)[-1]["outcome"] == "ATTEMPTED"
            intents.append(action)
            return action
        adapter.field_action_begin = before
        adapter.field_action_finish = audit.finish_field_action
        fields = [FieldResolution(field_id="PRIVATE_CONTROL_CANARY_" + key,
            selector="#" + key, label="PRIVATE_LABEL_CANARY", value="PRIVATE_VALUE_CANARY",
            status=ResolutionStatus.RESOLVED) for key in ("first", "second")]
        with pytest.raises(BrowserOwnershipError, match="outcome unknown"):
            adapter.apply_resolutions(fields)
        assert len(intents) == 1
        assert adapter.page.locator("#second").input_value() == ""
        assert adapter.page.evaluate("window.submits||0") == 0
    assert queue.field_actions(tid)[0]["outcome"] == "UNKNOWN_OUTCOME"
    with pytest.raises(RuntimeError, match="reconciliation"):
        queue.require_field_action_readbacks(attempt)
    for path in queue.root.rglob("*"):
        if path.is_file():
            payload = path.read_bytes()
            for canary in (b"PRIVATE_CONTROL_CANARY", b"PRIVATE_LABEL_CANARY", b"PRIVATE_VALUE_CANARY"):
                assert canary not in payload


def test_production_application_wires_daemon_audit_without_claiming_saved_draft(tmp_path):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    html = tmp_path / "application.html"
    html.write_text("""<!doctype html><body>
      <label>姓名<input id="name" name="full_name" required></label>
      <button type="button">Submit application</button>
    """, encoding="utf-8")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"fields": {"identity.full_name": {
        "value": "PRIVATE_VALUE_CANARY", "confidence": 1.0, "user_confirmed": True}}}))
    audit = _audit(queue, tid, owner, attempt)
    runner = ApplicationExecutor(html.as_uri(), profile,
        {"deepseek": {"enabled": False}}, audit_store=audit)
    plan = runner.run(max_pages=1)
    actions = queue.field_actions(tid)
    assert len(actions) == 1
    assert actions[0]["outcome"] == "DOM_READBACK_UNVERIFIED"
    assert (plan.metadata.get("review_certificate") or {}).get("status") != "PASS"
    assert "PRIVATE_VALUE_CANARY" not in json.dumps(actions)
    queue.require_field_action_readbacks(attempt)
    queue.finish_run_attempt(attempt, "RETURNED_UNVERIFIED")


def test_new_real_document_does_not_inherit_prior_same_selector_intent(tmp_path):
    queue, tid, owner, attempt, clock = _owned(tmp_path)
    pages = []
    for number in (1, 2):
        html = tmp_path / f"page-{number}.html"
        html.write_text("<!doctype html><body><label>姓名<input id='name'></label>",
                        encoding="utf-8")
        pages.append(html)
    audit = _audit(queue, tid, owner, attempt)
    field = FieldResolution(field_id="name", selector="#name", label="姓名",
        value="Synthetic", status=ResolutionStatus.RESOLVED)
    with GenericWebAdapter(pages[0].as_uri()) as adapter:
        adapter.field_action_begin = audit.begin_field_action
        adapter.field_action_finish = audit.finish_field_action
        assert adapter.apply_resolutions([field])[0]["ok"] is True
        adapter.page.goto(pages[1].as_uri())
        assert adapter.page.locator("#name").input_value() == ""
        assert adapter.apply_resolutions([field])[0]["ok"] is True
        assert adapter.page.locator("#name").input_value() == "Synthetic"
        # Re-entering the same control in that same document cannot create a
        # second journal-authorized operation, even with a new resolution value.
        field.value = "Do not replay"
        with pytest.raises(BrowserOwnershipError):
            adapter.apply_resolutions([field])
        assert adapter.page.locator("#name").input_value() == "Synthetic"
    actions = queue.field_actions(tid)
    assert len(actions) == 2
    assert len({row["field_sha256"] for row in actions}) == 2
    assert {row["outcome"] for row in actions} == {"DOM_READBACK_UNVERIFIED"}
